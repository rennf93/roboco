/*
 * as-git-user: run one command as RoboCo's workspace git user, and end it
 * and everything it started by a deadline.
 *
 *     as-git-user --timeout SECONDS --grace SECONDS -- PROGRAM [ARG...]
 *
 * The only caller is roboco/services/workspace_git.py (workspace_user_argv).
 * docker/orchestrator.Dockerfile compiles this file statically and installs
 * it as /usr/local/libexec/roboco/as-git-user, owner GIT_USER_UID:AGENT_GID,
 * mode 6750: set-user-id and set-group-id to the git user, and runnable only
 * by root and by members of AGENT_GID. Both ids are compiled in
 * (-DGIT_USER_UID=... -DAGENT_GID=...); 0 fails the build. The set-id bits
 * name the git user, not root, so the most a bug here can reach is that
 * user's identity.
 *
 * In order, it:
 *   1. reads its arguments: exactly the form above, TIMEOUT a whole number
 *      of seconds from 1, GRACE from 0, each at most nine digits.
 *   2. exits 126 unless its effective uid and gid are already the git user's,
 *      i.e. the set-id bits took effect. The kernel ignores them under
 *      no_new_privs and on a nosuid mount; going on there would run PROGRAM
 *      as the caller.
 *   3. root caller (real uid 0): clears the supplementary groups, raising
 *      CAP_SETGID from the permitted set a root caller keeps across the exec.
 *      Any other caller: exits 126 unless every supplementary group is
 *      AGENT_GID. Only CAP_SETGID can drop a group, so a group added to the
 *      service (docker run --group-add) makes git fail loudly here instead of
 *      reaching PROGRAM.
 *   4. setresgid(AGENT_GID x3), then setresuid(GIT_USER_UID x3), and checks
 *      all six ids. Leaving no uid at 0 clears the permitted and effective
 *      capability sets.
 *   5. sets no_new_privs, clears the ambient capability set, empties the
 *      inheritable, permitted and effective sets, and checks both.
 *   6. becomes a child subreaper (PR_SET_CHILD_SUBREAPER) and checks that
 *      /proc is readable: a process of the command's tree whose parent exits
 *      is reparented to this helper, not to the container's init.
 *   7. forks PROGRAM into a new session, which gives it its own process group
 *      that no process outside its tree can join (setpgid only joins a group
 *      of the caller's own session), and execvp(PROGRAM, ARGV) there with the
 *      environment and signal mask it was given. When that exec fails, the
 *      child writes "failed to run command 'PROGRAM': ..." and exits 127 (not
 *      found) or 126: the command's own result, not a refusal.
 *   8. supervises the tree. INT, QUIT, HUP and TERM it receives go to the
 *      command's process group. When PROGRAM exits, it waits for whatever
 *      PROGRAM left running, up to the deadline. TIMEOUT seconds after it
 *      started it sends TERM to the group and to each of its own children;
 *      GRACE seconds later it sends SIGKILL to the group and to each of its
 *      children, again whenever one of them dies, until none is left.
 *   9. reaps PROGRAM last and exits 124 when the deadline came while PROGRAM
 *      still ran; otherwise with PROGRAM's exit status, or by raising
 *      PROGRAM's terminating signal on itself (no core dump of its own), so the
 *      caller sees the status it would see without the helper.
 *
 * What it signals, and why that is the whole tree and nothing else. It only
 * signals (a) the command's process group, whose id is PROGRAM's pid, and
 * (b) its own children, each found in /proc/PID/stat naming this helper as
 * parent. A pid number is reused only once no process, zombie included, has
 * it as pid, group or session id. PROGRAM is reaped last, so (a) names the
 * command's group for the whole run; a child is never reaped before it is
 * signalled, so (b) names that child. Every process of the tree descends from
 * one of those children, and as subreaper this helper inherits the children
 * of each one that dies, including processes that left the group with setsid
 * or setpgid: repeating (b) after each death reaches all of them. Not
 * kill(-1, SIGKILL): as the git user that reaches every process of the git
 * user in the PID namespace, among them other workspace commands running at
 * the same time under their own helpers. Every process of the tree is the git
 * user: no_new_privs stops any set-id exec below.
 *
 * Every refusal writes one "as-git-user: ..." line to stderr and exits 126;
 * workspace_git.py reads that pair as the helper refusing. The helper reads
 * no environment variable and no file but /proc. As a set-id program it runs
 * in glibc's secure mode, which removes LD_*, GCONV_PATH, TMPDIR and similar
 * variables before main(): callers pass none of them, and a test pins that
 * the environment reaches PROGRAM unchanged.
 *
 * The command runs as the same uid as this helper, so it can signal it: a
 * command that kills its own helper leaves its tree unbounded. Callers bound
 * their wait regardless (workspace_user_wait_seconds).
 */

#define _GNU_SOURCE

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/capability.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#if !defined(GIT_USER_UID) || !defined(AGENT_GID)
#error "build with -DGIT_USER_UID=<uid> -DAGENT_GID=<gid>"
#endif
#if GIT_USER_UID <= 0
#error "GIT_USER_UID must not be root (0)"
#endif
#if AGENT_GID <= 0
#error "AGENT_GID must not be root's group (0)"
#endif

#define TIMED_OUT 124
#define REFUSED 126
#define CANNOT_RUN 126
#define NOT_FOUND 127
/* More supplementary groups than this cannot all be AGENT_GID. */
#define MAX_CHECKED_GROUPS 64
/* Far past any deadline, and small enough that no clock arithmetic
 * overflows. */
#define MAX_SECONDS_DIGITS 9
/* While killing, the tree is looked at again at least this often. */
#define KILL_RECHECK_NANOSECONDS 100000000L
#define NANOSECONDS_PER_SECOND 1000000000L

static const uid_t git_uid = GIT_USER_UID;
static const gid_t git_gid = AGENT_GID;
static const char usage[] =
    "usage: as-git-user --timeout SECONDS --grace SECONDS -- PROGRAM [ARG...]";

struct invocation {
    long timeout;
    long grace;
    char **command;
};

/* The command (PROGRAM's process), kept unreaped until the end. */
struct tree {
    pid_t command;
    bool command_exited;
};

enum phase { RUNNING, TERMINATING, KILLING };

static _Noreturn void refuse(const char *reason)
{
    fprintf(stderr, "as-git-user: %s\n", reason);
    exit(REFUSED);
}

static _Noreturn void refuse_errno(const char *step)
{
    fprintf(stderr, "as-git-user: %s: %s\n", step, strerror(errno));
    exit(REFUSED);
}

static long parse_seconds(const char *text, long minimum)
{
    size_t length = strlen(text);
    long value = 0;
    if (length == 0 || length > MAX_SECONDS_DIGITS)
        refuse(usage);
    for (size_t i = 0; i < length; i++) {
        if (text[i] < '0' || text[i] > '9')
            refuse(usage);
        value = value * 10 + (text[i] - '0');
    }
    if (value < minimum)
        refuse(usage);
    return value;
}

static struct invocation parse_arguments(int argc, char *argv[])
{
    struct invocation invocation;
    if (argc < 7 || strcmp(argv[1], "--timeout") != 0 ||
        strcmp(argv[3], "--grace") != 0 || strcmp(argv[5], "--") != 0)
        refuse(usage);
    invocation.timeout = parse_seconds(argv[2], 1);
    invocation.grace = parse_seconds(argv[4], 0);
    invocation.command = &argv[6];
    return invocation;
}

static void read_capabilities(struct __user_cap_data_struct data[2])
{
    struct __user_cap_header_struct header = {_LINUX_CAPABILITY_VERSION_3, 0};
    if (syscall(SYS_capget, &header, data) != 0)
        refuse_errno("capget");
}

static void write_capabilities(struct __user_cap_data_struct data[2])
{
    struct __user_cap_header_struct header = {_LINUX_CAPABILITY_VERSION_3, 0};
    if (syscall(SYS_capset, &header, data) != 0)
        refuse_errno("capset");
}

static void require_set_id_bits_honoured(void)
{
    if (geteuid() != git_uid || getegid() != git_gid)
        refuse("the set-id bits did not take effect (no_new_privs, a nosuid "
               "mount, or a wrong owner or mode): refusing to run as the "
               "caller");
}

static void clear_groups_as_root(void)
{
    struct __user_cap_data_struct caps[2];
    read_capabilities(caps);
    caps[0].effective |= caps[0].permitted & CAP_TO_MASK(CAP_SETGID);
    write_capabilities(caps);
    if (setgroups(0, NULL) != 0)
        refuse_errno("clearing the supplementary groups");
}

static void require_only_the_agent_group(void)
{
    gid_t groups[MAX_CHECKED_GROUPS];
    int count = getgroups(MAX_CHECKED_GROUPS, groups);
    if (count < 0)
        refuse_errno("reading the supplementary groups");
    for (int i = 0; i < count; i++) {
        if (groups[i] != git_gid)
            refuse("the caller holds a supplementary group other than the "
                   "agent group, which only root can drop: remove group_add "
                   "from the service");
    }
}

static void become_the_git_user(void)
{
    uid_t ruid, euid, suid;
    gid_t rgid, egid, sgid;
    if (setresgid(git_gid, git_gid, git_gid) != 0)
        refuse_errno("setresgid");
    if (setresuid(git_uid, git_uid, git_uid) != 0)
        refuse_errno("setresuid");
    if (getresuid(&ruid, &euid, &suid) != 0 || getresgid(&rgid, &egid, &sgid) != 0)
        refuse_errno("reading the ids back");
    if (ruid != git_uid || euid != git_uid || suid != git_uid ||
        rgid != git_gid || egid != git_gid || sgid != git_gid)
        refuse("the ids did not change to the git user's");
}

static void drop_every_capability(void)
{
    struct __user_cap_data_struct caps[2];
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0)
        refuse_errno("setting no_new_privs");
    if (prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0)
        refuse_errno("clearing the ambient capabilities");
    memset(caps, 0, sizeof(caps));
    write_capabilities(caps);
    read_capabilities(caps);
    for (int i = 0; i < 2; i++) {
        if (caps[i].effective || caps[i].permitted || caps[i].inheritable)
            refuse("capabilities are left after dropping them");
    }
    if (prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1)
        refuse("no_new_privs is not set");
}

static void become_a_child_subreaper(void)
{
    if (prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0)
        refuse_errno("becoming a child subreaper");
    if (access("/proc/self/stat", R_OK) != 0)
        refuse_errno("reading /proc, where the command's processes are found");
}

/* Blocks the signals the supervision waits for (sigtimedwait), returning
 * the caller's mask in `original` for the command. SIGCHLD goes back to its
 * default: a caller's SIG_IGN would make the kernel reap children unseen. */
static void block_the_watched_signals(sigset_t *watched, sigset_t *original)
{
    sigemptyset(watched);
    sigaddset(watched, SIGCHLD);
    sigaddset(watched, SIGINT);
    sigaddset(watched, SIGQUIT);
    sigaddset(watched, SIGHUP);
    sigaddset(watched, SIGTERM);
    if (signal(SIGCHLD, SIG_DFL) == SIG_ERR)
        refuse_errno("resetting SIGCHLD");
    if (sigprocmask(SIG_BLOCK, watched, original) != 0)
        refuse_errno("blocking the watched signals");
}

static pid_t start_command(char *command[], const sigset_t *original)
{
    pid_t pid = fork();
    if (pid < 0)
        refuse_errno("fork");
    if (pid > 0)
        return pid;
    if (setsid() < 0)
        refuse_errno("setsid");
    if (sigprocmask(SIG_SETMASK, original, NULL) != 0)
        refuse_errno("restoring the signal mask");
    execvp(command[0], command);
    int exec_errno = errno;
    fprintf(stderr, "failed to run command '%s': %s\n", command[0],
            strerror(exec_errno));
    _exit(exec_errno == ENOENT ? NOT_FOUND : CANNOT_RUN);
}

static struct timespec monotonic_now(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now;
}

static struct timespec seconds_after(struct timespec start, long seconds)
{
    start.tv_sec += seconds;
    return start;
}

static struct timespec nanoseconds_from_now(long nanoseconds)
{
    struct timespec moment = monotonic_now();
    moment.tv_nsec += nanoseconds;
    if (moment.tv_nsec >= NANOSECONDS_PER_SECOND) {
        moment.tv_sec += 1;
        moment.tv_nsec -= NANOSECONDS_PER_SECOND;
    }
    return moment;
}

/* The next of `watched` to arrive before `end`, or 0 once `end` has passed. */
static int next_signal_before(const sigset_t *watched, struct timespec end)
{
    for (;;) {
        struct timespec now = monotonic_now();
        struct timespec left = {end.tv_sec - now.tv_sec, end.tv_nsec - now.tv_nsec};
        if (left.tv_nsec < 0) {
            left.tv_sec -= 1;
            left.tv_nsec += NANOSECONDS_PER_SECOND;
        }
        if (left.tv_sec < 0)
            return 0;
        int sig = sigtimedwait(watched, NULL, &left);
        if (sig > 0)
            return sig;
        if (errno != EINTR && errno != EAGAIN)
            return 0;
    }
}

/* Whether PROGRAM has exited, seen without reaping it (WNOWAIT). */
static bool command_has_exited(struct tree *tree)
{
    siginfo_t info;
    if (tree->command_exited)
        return true;
    memset(&info, 0, sizeof(info));
    if (waitid(P_PID, (id_t)tree->command, &info,
               WEXITED | WNOHANG | WNOWAIT) == 0 &&
        info.si_pid == tree->command)
        tree->command_exited = true;
    return tree->command_exited;
}

/* The pid a /proc entry names, or 0 for any other entry. */
static pid_t pid_named(const char *name)
{
    long pid = 0;
    if (*name == '\0')
        return 0;
    for (const char *c = name; *c != '\0'; c++) {
        if (*c < '0' || *c > '9' || pid > 99999999L)
            return 0;
        pid = pid * 10 + (*c - '0');
    }
    return (pid_t)pid;
}

/* The parent of `pid` as /proc/PID/stat gives it, or 0 when it is gone. */
static pid_t parent_of(pid_t pid)
{
    char path[32];
    char line[256];
    char state;
    int parent;
    snprintf(path, sizeof(path), "/proc/%d/stat", (int)pid);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return 0;
    ssize_t length = read(fd, line, sizeof(line) - 1);
    close(fd);
    if (length <= 0)
        return 0;
    line[length] = '\0';
    /* "PID (NAME) STATE PPID ...": NAME may hold ')', the fields after it
     * are numbers. */
    const char *after_name = strrchr(line, ')');
    if (after_name == NULL || sscanf(after_name + 1, " %c %d", &state, &parent) != 2)
        return 0;
    return (pid_t)parent;
}

/* Sends `sig` (unless 0) to each child of this helper but PROGRAM, reaps
 * those that have exited, and returns how many have not. A child is signalled
 * before it is reaped, so its pid still names it. */
static int signal_and_reap_children(pid_t command, int sig)
{
    DIR *proc = opendir("/proc");
    struct dirent *entry;
    pid_t self = getpid();
    int running = 0;
    if (proc == NULL)
        return 1; /* cannot tell: count one as running, look again later */
    while ((entry = readdir(proc)) != NULL) {
        pid_t pid = pid_named(entry->d_name);
        if (pid <= 0 || pid == command || parent_of(pid) != self)
            continue;
        if (sig != 0)
            kill(pid, sig);
        if (waitpid(pid, NULL, WNOHANG) == 0)
            running++;
    }
    closedir(proc);
    return running;
}

/* Runs the tree to its end (see 8. above); returns whether the deadline came
 * while PROGRAM still ran. */
static bool supervise(struct tree *tree, const struct invocation *invocation,
                      const sigset_t *watched, struct timespec started)
{
    struct timespec deadline = seconds_after(started, invocation->timeout);
    struct timespec kill_at = seconds_after(deadline, invocation->grace);
    enum phase phase = RUNNING;
    bool timed_out = false;
    for (;;) {
        if (phase == KILLING)
            kill(-tree->command, SIGKILL);
        bool exited = command_has_exited(tree);
        if (exited || phase != RUNNING) {
            int running = signal_and_reap_children(
                tree->command, phase == KILLING ? SIGKILL : 0);
            if (exited && running == 0)
                return timed_out;
        }
        struct timespec until = kill_at;
        if (phase == RUNNING)
            until = deadline;
        else if (phase == KILLING)
            until = nanoseconds_from_now(KILL_RECHECK_NANOSECONDS);
        int sig = next_signal_before(watched, until);
        if (sig == SIGCHLD)
            continue;
        if (sig > 0) {
            if (phase != KILLING)
                kill(-tree->command, sig);
            continue;
        }
        if (phase == RUNNING) {
            timed_out = !command_has_exited(tree);
            kill(-tree->command, SIGTERM);
            signal_and_reap_children(tree->command, SIGTERM);
            phase = TERMINATING;
        } else {
            phase = KILLING;
        }
    }
}

static int reap(pid_t command)
{
    int status;
    while (waitpid(command, &status, 0) < 0) {
        if (errno != EINTR)
            refuse_errno("reaping the command");
    }
    return status;
}

/* Ends this helper the way PROGRAM ended: its exit status, or its
 * terminating signal raised here without a second core dump. With a core
 * limit of 0 the kernel still pipes a dump to a core_pattern program such as
 * systemd-coredump; with exactly 1 it aborts a piped dump, and a core file
 * cannot fit. */
static _Noreturn void end_like(int status)
{
    if (WIFSIGNALED(status)) {
        int sig = WTERMSIG(status);
        struct rlimit no_core = {1, 1};
        sigset_t only;
        setrlimit(RLIMIT_CORE, &no_core);
        signal(sig, SIG_DFL);
        sigemptyset(&only);
        sigaddset(&only, sig);
        sigprocmask(SIG_UNBLOCK, &only, NULL);
        raise(sig);
        exit(128 + sig);
    }
    exit(WEXITSTATUS(status));
}

int main(int argc, char *argv[])
{
    struct invocation invocation = parse_arguments(argc, argv);
    sigset_t watched, original;
    require_set_id_bits_honoured();
    if (getuid() == 0)
        clear_groups_as_root();
    else
        require_only_the_agent_group();
    become_the_git_user();
    drop_every_capability();
    become_a_child_subreaper();
    block_the_watched_signals(&watched, &original);
    struct timespec started = monotonic_now();
    struct tree tree = {start_command(invocation.command, &original), false};
    bool timed_out = supervise(&tree, &invocation, &watched, started);
    int status = reap(tree.command);
    if (timed_out)
        return TIMED_OUT;
    end_like(status);
}
