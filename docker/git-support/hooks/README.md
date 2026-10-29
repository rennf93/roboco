The ONLY hooks directory any orchestrator-side git command runs (`-c core.hooksPath=/opt/roboco/git/hooks`, roboco/services/workspace_git.py). A clone's own `.git/hooks` (and any `core.hooksPath` its config sets, e.g. husky's `.husky/_`) never runs: agents can write every clone.

git only executes files named after a hook (`pre-commit`, `post-commit`, ...); this README is never run.
