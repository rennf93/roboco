# Project & Workspace Tools

## Overview

There is **no** `roboco_project_*` or `roboco_workspace_*` agent tool. Agents do **not** create projects, manage git tokens, or ensure workspaces. Those are handled for you:

- **Workspaces are auto-cloned by the orchestrator** (`WorkspaceService`). Your per-agent clone of the project repo is created the first time you claim work on it — you never call a workspace tool. Branches are auto-created on `i_will_work_on()` / `claim_review()`; you don't run `git checkout` either.
- **Project registration and git-token management are operator actions** done through the control panel / HTTP API, not from inside an agent container. Tokens are encrypted at rest; the agent container never sees the PAT (it is injected into git operations server-side and scrubbed from URLs).

## What a task already tells you

A task carries its project linkage; you don't look it up with a tool. The task object you receive from `give_me_work()` / `triage()` includes the `project_id` (and the branch the flow verbs check out). Acceptance criteria and the project context come back inline on the Envelope.

## Inspecting the repo

Read-only git inspection is available through the `roboco-git-readonly` MCP server (developers and QA):

```python
# project_slug is optional on all four — omit it and your own project
# is used (from this agent's environment).
roboco_git_status()
roboco_git_log()
roboco_git_diff()
roboco_git_branch_list()
```

There is **no** `roboco_git_commit / _push / _checkout / _create_pr / _merge_pr` tool. Commits go through the `commit` content tool (auto- prefixed with `[task-id]`, auto-pushed by the choreographer); PRs open at `open_pr` time; merges are a PM `complete` operation.

## Finding project knowledge

To learn how a project's codebase is laid out or how a subsystem works, query the knowledge base rather than a project tool:

```python
roboco_kb_search(query="rate limiting redis", project="roboco-api",
                 index_types=["code", "documentation"])
roboco_ask_mentor(question="How is auth wired up in this project?")
```

## PM note: creating work

PMs create work with the `delegate` flow verb (a subtask under the current parent task), not a project/task-create tool. `delegate` takes an optional `project_id`; the parent task's project is inherited when you omit it. There is no agent-facing standalone project- or task-create tool.
