# RoboCo Deployment Guide

> **This guide has moved.** Deploying RoboCo is now documented in the full docs site at **[roboco.dev/docs](https://roboco.dev/docs)** (source under [`docs/`](docs/)).

Jump straight to:

- **[Install & first run](docs/get-started/installation.md)** — the quickest path: clone, set two secrets, `docker compose up`.
- **[Deployment](docs/deploy/deployment.md)** — the production guide: compose files, the single-origin nginx, host-path mounts, data persistence, secure mode, and startup ordering.
- **[Environment reference](docs/deploy/env-reference.md)** — every `ROBOCO_*` setting with its default and purpose.
- **[Data & migrations](docs/deploy/data-and-migrations.md)** — the schema, pgvector, and how the stack self-migrates.
- **[Troubleshooting](docs/troubleshooting/common-issues.md)** — the common deploy snags and their fixes.

Preview the site locally with `make serve-docs`.
