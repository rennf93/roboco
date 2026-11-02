# Token Factory Sandboxes

Token Factory Sandboxes are armed on this deployment. `run_sandbox_tests(command=...)` runs a test command in a fresh, disposable Nebius microVM (Python 3.13 by default) against the task's committed code, and every run is visible in the Token Factory console.

- **QA:** after your local checks, run the tests this task added or changed there and cite the exit code in your `pass` / `fail` notes. The verb archives the developer's pushed branch for you. The image is bare Python, so the command must first install the test runner the files need (pytest, plus anything they import), then run only those files with the repo's coverage options disabled (`-o addopts=''`).
- **Developer:** run the same command from your worktree before `i_am_done` to catch a failure before QA does.
- A non-zero exit is a real test failure: QA fails the review, a developer fixes it. A sandbox error envelope is not a verdict: retry once, then note it and rely on your local evidence.
