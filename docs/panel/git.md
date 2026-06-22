# Git

The **Git** page (`/git`) is a real working git client over any of your registered projects. You pick a project (and optionally a task), browse the live state of its workspace — status, branches, commit log, and diffs — and run git operations directly from the panel. It's how you inspect what the agents are doing in git, and step in by hand when you need to.

## Picking a project

Choose a project from the dropdown to load its repository. The selection is held in the URL, so a given project's git view is shareable and back-button-safe. Until you pick one, the page prompts you to choose. You can also scope to a specific task.

!!! note "The orchestrator must be up"
    Git operations run server-side against the agent workspaces, so this page needs the orchestrator running. With the backend down it shows an offline state with a retry rather than failing silently.

## Browsing the repository

Once a project is loaded you get live, refreshable views:

- **Status** — staged and unstaged changes in the workspace.
- **Branches** — the branch list (including remotes).
- **Log** — the recent commit history.
- **Diff** — the staged/unstaged diff viewer for the working tree.

A **Refresh** re-pulls status, log, and branches together.

## Operations you can run

The page wires up the full set of git operations against the selected project:

| Operation | What it does |
|-----------|--------------|
| **Commit** | commit staged changes (returns the new commit hash) |
| **Push** | push the current branch to the remote |
| **Create branch** | cut a new hierarchical branch for a task (by branch type) |
| **Checkout** | switch to an existing branch |
| **Create PR** | open a pull request (returns the PR number) |
| **Merge PR** | merge a PR into its target branch |
| **Pull** | pull the current branch from the remote |
| **Fetch** | fetch from the remote |
| **Rebase** | rebase the current branch onto a target branch |

Each action confirms with a toast on success (or a clear failure message) and refreshes the relevant view.

!!! warning "This is the live repository"
    These operations act on the real workspace clone and your real remote — a merge here merges for real. The same branch/PR/merge actions are also available, task-scoped, from the [Task Detail page](./tasks-and-kanban.md#ceo-god-mode), where they run as agent id `ceo`. Remember that **only the CEO merges to master**; see [the merge model](../company/merge-model.md).

Git authentication uses the project's encrypted GitHub token, which you set when you [register the project](../get-started/first-project.md#the-github-token) — the panel never holds or shows the PAT.

## Next

→ [Agents & work sessions](./agents-and-work-sessions.md) to tie branches back to the agents that cut them, or [Projects & products](./projects-and-products.md) to manage the repositories themselves.
