---

name: Bug report
about: Create a report to help us improve RoboCo
title: '[BUG] '
labels: bug
assignees: ''
---

Bug Description
===============

A clear and concise description of what the bug is.

___

Steps To Reproduce
------------------

Steps to reproduce the behavior:

1. Start the stack with '...'
2. Trigger '....' (API call, agent verb, panel action, ...)
3. See error

___

Expected Behavior
-----------------

A clear and concise description of what you expected to happen.

___

Actual Behavior
---------------

What actually happened, including error messages, stack traces, or logs.

___

Affected Subsystem
------------------

Which part of RoboCo is involved (check all that apply):

- [ ] api (`roboco/api`)
- [ ] services (`roboco/services`)
- [ ] gateway (`roboco/services/gateway`)
- [ ] orchestrator (`roboco/runtime`)
- [ ] enforcement (`roboco/enforcement`)
- [ ] db / models (`roboco/db`, `roboco/models`)
- [ ] agents (`agents/`)
- [ ] mcp (`roboco/mcp`)
- [ ] panel (`panel/`)
- [ ] alembic (`alembic/`)
- [ ] Not sure

___

Environment
-----------

- RoboCo version / image tag: [e.g. 0.1.0]
- Python version: [e.g. 3.13.1]
- Node version (panel issues): [e.g. 20.x]
- Docker / Docker Compose version: [e.g. 27.x / v2.x]
- OS: [e.g. Ubuntu 22.04, macOS 15]
- Deployment: [docker-compose / local `make dev` / other]

___

Logs / Configuration
--------------------

Relevant logs, tracebacks, and any non-secret `ROBOCO_*` settings:

```text
(paste here)
```

___

Additional Context
------------------

Add any other context about the problem here. For example:

- Is this happening in production or development?
- Does it happen consistently or intermittently?
- Have you tried any workarounds?
