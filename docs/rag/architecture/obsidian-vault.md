# Obsidian Vault (V1)

## What It Is

RoboCo can project its own state into a human-readable [Obsidian](https://obsidian.md) vault — tasks, journal entries, and A2A thread digests as wikilinked markdown notes — so the CEO can browse the org's memory with a normal notes app instead of the panel alone. It is a **rebuildable projection**, not a second source of truth: every note is derived from DB state and can always be regenerated from scratch. Implemented in `roboco/services/vault_writer.py` (pure markdown materializer), `roboco/services/vault_assembly.py` (DB → materializer dataclasses), `roboco/services/vault_intake_engine.py` (the inbox watcher), and `roboco/vault.py` (the `rebuild`/`relocate` CLI).

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_OBSIDIAN_VAULT_ENABLED` | `false` (both compose files set `true`) | Master switch. Off = no note is ever written, `curate_vault` and `python -m roboco.vault` both refuse. |
| `ROBOCO_VAULT_PATH` | `/data/vault` | Root directory the vault materializes into. |
| `ROBOCO_VAULT_INTAKE_ENABLED` | `false` (both compose files set `true`) | The `#roboco`-tag inbox watcher (see below) — requires the master switch also on. |

## What agents actually touch

Almost everything here is transparent to a working agent — notes get written or patched as a side effect of normal verbs (a status transition, a `note()` call, a `dm()`), never something you call yourself. There are exactly two places an agent interacts with the vault directly:

1. **The Auditor's `curate_vault(task_id, narrative)`** — spawned once per completed root task to write the one piece of vault content that isn't mechanically derivable from DB columns: a narrative paragraph on the task's note. See `docs/rag/roles/auditor.md`.
2. **The vault-intake watcher turning a CEO-authored note into a task you might get delegated.** If the CEO tags a note `#roboco` in the vault's inbox folder, a default-off watcher (`ROBOCO_VAULT_INTAKE_ENABLED`) drafts it into a board-review task (`source=vault_note`) — same shape as a chat-confirmed intake draft, Product-Owner-assigned, `team=board`. It never starts work directly: the board reviews it and only the CEO's `approve_and_start` hands it to the Main PM for real delegation. If you end up working a task with `source=vault_note`, its origin was a note the CEO wrote in their own vault, not a chat.

Everything else — note layout, link stability, the rebuild CLI — is infrastructure you don't need to reason about to do your job; the rest of this doc is here for completeness, not because you'll call any of it.

## Layout and link stability

```
RoboCo/
  Tasks/<project-slug>/<title> (<id8>).md
  Journals/<agent-slug>/<date> <title> (<id8>).md
  A2A/<date> <agents> (<thread-id8>).md
  Agents/<slug>.md
  _meta/          (shipped Dataview/Kanban/graph-group dashboards)
```

Every note carries `aliases: [<id8>]` in its frontmatter, so a cross-link is always written as `[[<id8>|<title>]]` — Obsidian resolves it by alias regardless of the target's current filename. A task title edit updates the note's own title line without ever renaming the file or breaking a link elsewhere in the vault. Private journal entries (`is_private`) are excluded from the projection, same as the shared RAG corpus.

## What triggers a write

Best-effort event seams patch or append on the relevant transition — never a gate, never something that can block the real action:

- A task status transition patches the note's frontmatter (status/team/PR) in place, if the note already exists.
- A `note()` (journal entry) writes one immutable file per entry.
- A `dm()` (A2A message) appends to a per-thread digest file.

None of these fail your call if the vault write itself fails — it's logged and swallowed.

## Rebuild and relocate

`python -m roboco.vault rebuild` re-projects every live entity from the DB from scratch (preserving any existing task's Auditor-authored narrative, since that isn't derivable) and materializes the shipped `.obsidian/` config + `_meta/` dashboards if not already present. `python -m roboco.vault relocate <path>` moves the tree — useful for grafting `RoboCo/` into an existing personal vault without touching that vault's own config. Both are operator/CEO commands, not something an agent runs.

## Related

- `docs/rag/roles/auditor.md` — the `curate_vault` verb
- `docs/rag/architecture/config-reference.md` — full env var table
- `docs/rag/architecture/x-engine.md` — the sibling engine whose injection-screening guard (`screen_external_text`) the vault-intake watcher shares
