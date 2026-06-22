# Register your first project

A **project** is a git repository RoboCo is allowed to work on, plus the configuration that tells the company how to build and check it. Until you register one, the agents have nowhere to put their work. You register projects in the panel under **Projects → New**.

## What a project needs

| Field | What it is |
|-------|-----------|
| **Name** | Human-readable label for the repo. |
| **Slug** | URL-safe short name. It becomes the top of the workspace path (`{slug}/{team}/{agent}/`) and shows up in branch names. |
| **Git URL** | The clone URL. HTTPS is the common case and **requires a token** (below). |
| **GitHub token (PAT)** | A Personal Access Token, stored encrypted. Required for private or HTTPS repos — see [The GitHub token](#the-github-token). |
| **Assigned cell** | Which delivery cell owns this repo: Backend, Frontend, or UX/UI. |
| **Default branch** | The branch PRs ultimately target. **Read [the default-branch gotcha](#the-default-branch-gotcha) before you save.** |
| **Gate commands** *(optional)* | Per-project test / lint / format / typecheck / build commands, and a fast pre-submit `quality_command`. See [Gate commands](#gate-commands). |

## The GitHub token

Agents clone your repository and open pull requests on it, so they need a **GitHub Personal Access Token** with permission to do that.

- **Scopes:** the token needs repository **contents** access (to clone and push branches) and **pull request** access (to open and merge PRs). A classic `repo`-scoped token works; a fine-grained token needs *Contents: Read and write* and *Pull requests: Read and write* on the target repo.
- **It's encrypted and write-only.** The token is encrypted at rest the moment you save it (with your `ROBOCO_ENCRYPTION_KEY`) and the API **never returns it** — the panel only shows whether a token is set, not its value.
- **Rotating or clearing it:** in **Edit Project**, entering a new token replaces it, an empty field clears it, and leaving it untouched keeps the current one.

!!! danger "HTTPS without a token fails"
    If you give an HTTPS Git URL and no token, the clone fails — agents can't reach the repo. Set the token when you create the project.

!!! warning "The encryption key is load-bearing"
    Every stored project token is encrypted with `ROBOCO_ENCRYPTION_KEY`. If you ever change that key, all stored tokens become undecryptable and you'll have to re-enter every one. Pick it once at install and keep it backed up.

## The default-branch gotcha { #the-default-branch-gotcha }

The **Create Project** dialog defaults the branch to `main`, but several places in the backend assume `master`. **Set this field explicitly to match your repository's real default branch** (`main` or `master`) rather than trusting the pre-filled value. Getting it wrong is the most common first-run snag — branches get cut from, and PRs target, the wrong base.

## Gate commands { #gate-commands }

A developer agent runs a quality gate against its own work *before* it submits for QA. By default that's a sensible lint + typecheck pair, but you'll get far better results by pointing RoboCo at your repository's *real* checks:

- **`quality_command`** — the fast pre-submit gate, run at the moment an agent says it's done (for example `make gate`). If you set this, it replaces the default lint/typecheck pair. Keep it fast; it runs on every submission.
- **`test_command`, `lint_command`, `format_command`, `typecheck_command`, `build_command`** — the individual commands for the cell's QA and CI steps.

Setting these so they mirror what *you* would run locally is the single biggest lever on output quality: the company gates itself exactly the way you would.

## Sandboxing agents away from a repo

If you want to make sure agents can never point a project at a particular repository — RoboCo's own source, say, during a test run — set `ROBOCO_PROTECTED_GIT_URLS` to a comma-separated list of URL substrings. Creating or updating a project whose Git URL matches one is rejected.

## What happens under the hood

You don't have to manage any of this, but it's worth knowing what registering a project sets in motion:

- The first time an agent is assigned work on the project, RoboCo clones the repo into that agent's own workspace under `ROBOCO_WORKSPACES_ROOT` (default `/data/workspaces`). **Every agent gets its own clone**, so they work in parallel without stepping on each other. That directory is the disk to provision and back up.
- Right after cloning, RoboCo **scrubs the token out of the clone's git config** and verifies no token byte survived anywhere under `.git/` — destroying the workspace if one did. Your PAT never lives inside an agent container.

## Next

→ **[Your first task](first-task.md)** — hand the company something to build.
