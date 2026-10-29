/*
 * as-git-user: run one program as RoboCo's workspace git user.
 *
 *     as-git-user PROGRAM [ARG...]
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
 *   1. exits 126 unless its effective uid and gid are already the git user's,
 *      i.e. the set-id bits took effect. The kernel ignores them under
 *      no_new_privs and on a nosuid mount; going on there would run PROGRAM
 *      as the caller.
 *   2. root caller (real uid 0): clears the supplementary groups, raising
 *      CAP_SETGID from the permitted set a root caller keeps across the exec.
 *      Any other caller: exits 126 unless every supplementary group is
 *      AGENT_GID. Only CAP_SETGID can drop a group, so a group added to the
 *      service (docker run --group-add) makes git fail loudly here instead of
 *      reaching PROGRAM.
 *   3. setresgid(AGENT_GID x3), then setresuid(GIT_USER_UID x3), and checks
 *      all six ids. Leaving no uid at 0 clears the permitted and effective
 *      capability sets.
 *   4. sets no_new_privs, clears the ambient capability set, empties the
 *      inheritable, permitted and effective sets, and checks both.
 *   5. execvp(PROGRAM, ARGV) with the environment it was given: exit 127 when
 *      PROGRAM is not found, 126 when it cannot be run.
 *
 * Every refusal writes one "as-git-user: ..." line to stderr and exits 126;
 * workspace_git.py reads that pair as the helper refusing. The helper reads
 * no file and no environment variable. As a set-id program it runs in glibc's
 * secure mode, which removes LD_*, GCONV_PATH, TMPDIR and similar variables
 * before main(): callers pass none of them, and a test pins that the
 * environment reaches git unchanged.
 */

#define _GNU_SOURCE

#include <errno.h>
#include <grp.h>
#include <linux/capability.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/types.h>
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

#define REFUSED 126
#define NOT_FOUND 127
/* More supplementary groups than this cannot all be AGENT_GID. */
#define MAX_CHECKED_GROUPS 64

static const uid_t git_uid = GIT_USER_UID;
static const gid_t git_gid = AGENT_GID;

static void refuse(const char *reason)
{
    fprintf(stderr, "as-git-user: %s\n", reason);
    exit(REFUSED);
}

static void refuse_errno(const char *step)
{
    fprintf(stderr, "as-git-user: %s: %s\n", step, strerror(errno));
    exit(REFUSED);
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

int main(int argc, char *argv[])
{
    if (argc < 2)
        refuse("usage: as-git-user PROGRAM [ARG...]");
    require_set_id_bits_honoured();
    if (getuid() == 0)
        clear_groups_as_root();
    else
        require_only_the_agent_group();
    become_the_git_user();
    drop_every_capability();
    execvp(argv[1], &argv[1]);
    int exec_errno = errno;
    fprintf(stderr, "as-git-user: %s: %s\n", argv[1], strerror(exec_errno));
    return exec_errno == ENOENT ? NOT_FOUND : REFUSED;
}
