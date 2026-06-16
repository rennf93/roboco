# PR Reviewer Agent
#
# A read-only reviewer: fetches a PR diff via the GitHub API, greps the
# codebase, and posts one change-request. It never runs or builds the code (the
# trust boundary — execution only happens later in a dev cell during supersede),
# so it needs no language toolchain or test runner beyond the base image. It
# gets its own image for parity with every other agent and so the role is
# explicit on the compose + release surface. Keeps the base `claude` entrypoint
# — it is dispatched per review task like the dev/QA agents, not a persistent
# SDK driver like intake/secretary.

FROM roboco-agent-base

LABEL role="pr-reviewer"
LABEL description="Read-only PR reviewer - reviews inbound external PRs and posts one change-request"
