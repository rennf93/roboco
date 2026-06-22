# Bootstrap & seeds

A fresh RoboCo database isn't an empty shell — it comes pre-populated with the whole org chart: every agent, every communication channel, and the welcome messages that establish the channels. This page covers what `make db-init` seeds, and the console entry points that run the bootstrap.

## `make db-init` — seed a fresh instance

The one command you need to bring a clean database to life:

```bash
make db-init
```

This runs `python -m roboco.cli --db-only`, which enables the schema (the [migration chain](./data-and-migrations.md)) and then seeds the company in a single transaction.

!!! tip "It's idempotent — safe to re-run"
    Every seed operation skips what already exists (agents and channels by slug; welcome messages are skipped if the channel already has any). Running `make db-init` against an already-seeded database is a harmless no-op, so you can re-run it any time without fear of duplicates.

## What gets seeded

| Seeded data | Detail |
|-------------|--------|
| **26 agent rows** | The 25 AI agents **plus the human CEO** ("Renzo"), each with a stable static UUID. A `system` sentinel row is also appended (it's the FK target for system-authored messages and lives outside the team enum). |
| **11 channels** | The 3 cell channels (`#backend-cell`, `#frontend-cell`, `#uxui-cell`), the 4 cross-role channels (`#dev-all`, `#qa-all`, `#pm-all`, `#doc-all`), and the management/special channels (`#main-pm-board`, `#board-private`, `#announcements`, `#all-hands`). |
| **Channel memberships** | Who can read and write each channel, plus the **Auditor's silent-observer access** to every channel. |
| **Welcome messages** | An opening message seeded into `#announcements`, `#all-hands`, and the three cell channels (each first spins up the backing group and session). |

The roster and the seeded UUIDs are derived from the foundation catalog (`roboco/foundation/identity.py` for agents, `roboco/foundation/policy/communications.py` for channels), so the seed data and the runtime role/permission rules can never drift apart. For the full org chart and who sits in which channel, see [Org & roles](../company/org-and-roles.md).

## The console entry points

`make db-init` is a thin wrapper over the CLI. The supported way to invoke RoboCo directly is the module form:

```bash
python -m roboco.cli --db-only          # seed the DB and exit (what make db-init runs)
python -m roboco.cli                     # seed + start the full orchestrator stack
python -m roboco.cli --skip-db           # start the stack against an already-seeded DB
python -m roboco.cli --spawn be-dev-1 fe-dev-1   # also spawn the named agents on boot
```

`python -m roboco.cli` is what the orchestrator container runs and what `make db-init` / `make` targets call. Full system start does more than seed: it brings up the Redis event bus, constructs the orchestrator, starts the API under uvicorn, polls `/health` until the FastAPI lifespan finishes indexing (up to ~2 minutes), then begins dispatching.

!!! note "`python -m roboco.cli` is the working invocation"
    `pyproject.toml` declares two console scripts, `roboco` and `roboco-bootstrap`. Use `python -m roboco.cli` (or `make db-init`) — that's the invocation the Makefile and the container use and the one that's verified to work. `roboco-bootstrap` maps to the same bootstrap routine; the bare `roboco` console script is not the supported path.

## Required configuration before first boot

Bootstrap itself needs almost nothing, but the orchestrator will refuse to start without two secrets:

- **`ROBOCO_ENCRYPTION_KEY`** — the Fernet key that encrypts project GitHub tokens. Its config default is an empty string, but startup is mandatory: no key, no boot.
- **`ROBOCO_AGENT_AUTH_SECRET`** — the HMAC secret that signs agent tokens. Unset, every token is treated as unsigned.

Everything else has a sensible default. See the [environment reference](./env-reference.md) for the full list, and [Deployment](./deployment.md) for the compose host-path mounts.

## Next

→ [Data & migrations](./data-and-migrations.md) — the entities you just seeded, and how the schema stays current. → [Org & roles](../company/org-and-roles.md) — the workforce these seeds create.
