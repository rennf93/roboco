## Purpose
The CEO-facing intake and chief-of-staff slice. PrompterService turns a confirmed live-intake structured draft (or a MegaTask batch of drafts) into real Task rows, routing ownership/team and sequencing collision-free waves. PrompterLiveRegistry is the in-process bridge that relays a live chat between a spawned prompter/secretary container and the panel (SSE stream + turn delivery + park/idle lifecycle). SecretaryService reads company state and executes or gates the CEO's directives (relay/announce/charter/pitch/task-control), recording every directive auditably.

## Files

| Path | Role | LOC |
|---|---|---|
| roboco/services/prompter.py | PrompterService: create tasks from confirmed intake drafts (single + MegaTask batch), route owning team, sequence drafts into waves; plus pure description/readiness helpers | 1066 |
| roboco/services/prompter_live.py | PrompterLiveRegistry: process-wide singleton bridging live intake/secretary chat between panel (SSE) and spawned container (HTTP turn), with open/close/park/idle-reap lifecycle | 235 |
| roboco/services/secretary.py | SecretaryService: read company state + submit/confirm/reject gated CEO directives (relay/announce/charter/pitch/task-control), persisted in secretary_directives | 266 |

## Key Symbols

| Name | Kind | File:Line | Responsibility |
|---|---|---|---|
| ReadinessTag | dataclass | roboco/services/prompter.py:71 | Parsed contents of an assistant turn's trailing roboco-meta JSON block (covered, ready, scale) |
| BatchPlacement | dataclass | roboco/services/prompter.py:80 | Where a draft sits in a MegaTask batch (parent_task_id, batch_id, sequence, team_override) |
| PrompterService | class | roboco/services/prompter.py:97 | Create tasks from confirmed intake drafts; pure draft/description helpers + DB-backed create |
| PrompterService._session | property | roboco/services/prompter.py:108 | Return the AsyncSession or raise ServiceError if constructed without one |
| PrompterService._assignee_is_board | method | roboco/services/prompter.py:118 | True if agent_id is a board/advisory role (PO / HoM / Auditor) |
| PrompterService._validate_draft_target | staticmethod | roboco/services/prompter.py:125 | A draft targets exactly one of project/product/per-cell-map, or none for an umbrella |
| PrompterService._resolve_owning_team | method | roboco/services/prompter.py:163 | Route owning team: team_override wins; if no product: multi-cell map (≥2 cells) -> MAIN_PM else lead cell; if product: board assignee -> BOARD else MAIN_PM (product/board routing checked BEFORE multi-cell force) |
| PrompterService._validate_and_coerce_draft | method | roboco/services/prompter.py:196 | Validate title+AC, coerce list fields (acceptance_criteria/what_this_builds/notes/the_work[].items) to list[str] in place |
| PrompterService._resolve_draft_assignee | method | roboco/services/prompter.py:243 | Explicit confirm-button assignment wins; else fall back to draft.assigned_to UUID |
| PrompterService._coerce_pm_code_to_planning | method | roboco/services/prompter.py:256 | Coerce code->planning when owner is a coordination PM role; two layers: team-based (main_pm_cannot_own_code) then assignee-based (pm_cannot_own_code); issue-resolution carve-out never applies for new intake tasks |
| PrompterService.create_task_from_draft | method | roboco/services/prompter.py:293 | Operate on a _copy_draft copy (caller never mutated), compose description, validate target, coerce enums, route team, coerce PM+code->planning via _coerce_pm_code_to_planning, persist via TaskService.create |
| PrompterService.confirm_live_draft | method | roboco/services/prompter.py:368 | Confirm a live-intake single draft -> create at PENDING assigned to product-owner (board) or main-pm route; return task id |
| PrompterService._sequence_drafts | method | roboco/services/prompter.py:419 | Build DraftSurface list and run SequencingService.analyze into waves; SequencingError -> ValidationError 400 |
| PrompterService.preview_batch | method | roboco/services/prompter.py:459 | Compute MegaTask waves+warnings WITHOUT creating (panel pre-confirm preview) |
| PrompterService._validate_batch_scope | staticmethod | roboco/services/prompter.py:473 | Each draft targets scoped repos via cell map or top-level project_id; union across drafts spans >=2 distinct projects |
| PrompterService.confirm_live_batch | method | roboco/services/prompter.py:524 | Create MegaTask umbrella + N sequenced root-subtasks, wire dependency edges; return umbrella_id/root_ids/waves/warnings |
| PrompterService.update_live_draft | method | roboco/services/prompter.py:628 | Apply a board-informed re-draft to an existing task in place; route via approve_and_start or re-board (clear board_review_complete) |
| PrompterService._resolve_uuid_field | staticmethod | roboco/services/prompter.py:676 | Parse draft_data[key] as UUID; None if absent, ValidationError if malformed |
| PrompterService._lead_cell_team | staticmethod | roboco/services/prompter.py:690 | Owner of a single-cell task: first valid Team in the_work, else default |
| PrompterService._coerce_draft_enums | staticmethod | roboco/services/prompter.py:704 | Coerce team/task_type/nature/complexity to valid enums; default on invalid/missing so confirm never hard-fails |
| PrompterService._coerce_priority | staticmethod | roboco/services/prompter.py:734 | Coerce priority (word or number) to int 0-3, default 2 |
| parse_readiness | function | roboco/services/prompter.py:786 | Split assistant reply into (clean_text, ReadinessTag) from trailing roboco-meta JSON fence |
| _as_work_entry | function | roboco/services/prompter.py:818 | Normalize a the_work entry (bare string -> {team:str}) so .get works on all entries |
| _cell_teams | function | roboco/services/prompter.py:835 | Distinct cell team values present in the_work, in order |
| _draft_cell_map | function | roboco/services/prompter.py:846 | Per-cell (team, project_id) map from the_work entries; de-duped by team; the multi-cell MegaTask root-subtask seam |
| derive_scale | function | roboco/services/prompter.py:882 | 'multi' when >1 cell participates, else 'single' |
| _clean_list | function | roboco/services/prompter.py:947 | coerce_str_list wrapper: trimmed non-empty string items, extracting dict-wrapped text |
| _copy_draft | function | roboco/services/prompter.py:956 | Shallow copy of draft dict with the_work unit dicts also copied, so _validate_and_coerce_draft cannot mutate the caller's dict |
| _text | function | roboco/services/prompter.py:896 | Trimmed string from a possibly-missing scalar |
| _bullets | function | roboco/services/prompter.py:901 | Render a markdown bullet list |
| _cell_label | function | roboco/services/prompter.py:906 | Display label for a team value |
| _render_work_entry | function | roboco/services/prompter.py:911 | Render one cell's slice: bold heading + summary + deliverable bullets |
| _render_the_work | function | roboco/services/prompter.py:926 | Render The Work section, prepending a board-led lead line when multi-cell |
| _section | function | roboco/services/prompter.py:938 | Append a markdown section when its body is non-empty |
| format_board_briefing | function | roboco/services/prompter.py:944 | Render board review entries into a markdown briefing to seed a re-draft intake session |
| compose_redraft_message | function | roboco/services/prompter.py:969 | Seed message for a re-draft session: current draft + board feedback |
| compose_description | function | roboco/services/prompter.py:985 | Deterministically build the markdown description from structured fields; fall back to model description if too sparse (<20 chars) |
| _compose_umbrella_draft | function | roboco/services/prompter.py:1015 | Build the branchless umbrella draft from batch + wave plan; task_type=planning |
| get_prompter_service | function | roboco/services/prompter.py:1059 | Factory: construct PrompterService with optional db session |
| LiveIntakeSession | dataclass | roboco/services/prompter_live.py:39 | One live chat: session_id, agent_id, asyncio queue, closed flag, parked task_id, last_activity timestamp |
| PrompterLiveRegistry | class | roboco/services/prompter_live.py:58 | Tracks live intake/secretary sessions; bridges panel<->container via push/stream/deliver; lifecycle open/close/park |
| PrompterLiveRegistry.open | method | roboco/services/prompter_live.py:68 | Register a live session; idempotent (returns existing un-closed session instead of orphaning its SSE queue) |
| PrompterLiveRegistry.get | method | roboco/services/prompter_live.py:88 | Return the session or None |
| PrompterLiveRegistry.is_alive | method | roboco/services/prompter_live.py:91 | True when a live un-closed session exists (panel reload reconnect decision) |
| PrompterLiveRegistry.close | method | roboco/services/prompter_live.py:101 | End a session: pop, mark closed, push _CLOSE sentinel to unblock the SSE stream |
| PrompterLiveRegistry.close_by_agent | method | roboco/services/prompter_live.py:110 | Close every live session bound to agent_id (forced kill); optional final error event; returns closed ids |
| PrompterLiveRegistry.park | method | roboco/services/prompter_live.py:129 | Mark session parked awaiting board review of task_id (keeps it alive for in-context re-draft) |
| PrompterLiveRegistry.find_by_task | method | roboco/services/prompter_live.py:148 | Return the live un-closed session parked for task_id, if any |
| PrompterLiveRegistry.push | method | roboco/services/prompter_live.py:157 | Queue one agent event for SSE; bump last_activity; False if no/gone session |
| PrompterLiveRegistry.idle_session_ids | method | roboco/services/prompter_live.py:166 | Return (session_id, agent_id) idle past threshold; excludes closed and board-parked sessions |
| PrompterLiveRegistry.stream | method | roboco/services/prompter_live.py:185 | Async generator yielding queued events until _CLOSE sentinel |
| PrompterLiveRegistry.deliver | method | roboco/services/prompter_live.py:198 | POST the human's text to the container's /turn receiver; bump last_activity; debug-log transient failures |
| get_live_registry | function | roboco/services/prompter_live.py:230 | Process-wide singleton accessor (lazily instantiates PrompterLiveRegistry) |
| SecretaryService | class | roboco/services/secretary.py:54 | Read company state + execute/queue CEO directives; BaseService subclass bound to a session |
| SecretaryService.read_company_state | method | roboco/services/secretary.py:63 | Aggregate goals + task counts + proposed pitches + pending directives for the CEO dashboard |
| SecretaryService.read_task | method | roboco/services/secretary.py:79 | Read a single task's id/title/status/team/assignee/description or NotFoundError |
| SecretaryService.get_directive | method | roboco/services/secretary.py:96 | Fetch a directive row by id or None |
| SecretaryService.list_directives | method | roboco/services/secretary.py:104 | List directives ordered by requested_at desc, optional status filter |
| SecretaryService.submit_directive | method | roboco/services/secretary.py:115 | Validate payload; persist row; if gated -> notify CEO pending + return; else run immediately |
| SecretaryService.confirm_directive | method | roboco/services/secretary.py:134 | CEO confirms a pending directive: set decided_by, run it |
| SecretaryService.reject_directive | method | roboco/services/secretary.py:142 | CEO rejects a pending directive: REJECTED + decided_by/at + result reason |
| SecretaryService.to_dict | staticmethod | roboco/services/secretary.py:153 | Serialize a directive row to a dict for API response |
| SecretaryService._pending_or_raise | method | roboco/services/secretary.py:171 | Fetch directive; NotFoundError if missing, ConflictError if not PENDING |
| SecretaryService._validate_payload | staticmethod | roboco/services/secretary.py:182 | Require the per-kind payload keys from _REQUIRED_PAYLOAD else ValidationError |
| SecretaryService._run | method | roboco/services/secretary.py:188 | Execute a directive: set result, EXECUTED on success, FAILED+error message on caught domain errors |
| SecretaryService._execute | method | roboco/services/secretary.py:204 | Dispatch by kind: relay/announce post to channel, update_charter upsert, approve_pitch approve+provision, else _control_task |
| SecretaryService._control_task | method | roboco/services/secretary.py:229 | Task control: start (approve_and_start), cancel, or override status with CEO as actor |
| SecretaryService._notify_ceo_pending | method | roboco/services/secretary.py:250 | Send an ack notification to the CEO that a gated directive awaits confirmation |
| get_secretary_service | function | roboco/services/secretary.py:263 | Factory: construct SecretaryService bound to a session |

## Data Flow
Intake: the orchestrator spawns a prompter container and calls PrompterLiveRegistry.open(session_id, INTAKE_AGENT_ID); the panel SSE endpoint calls stream() and the message endpoint calls deliver() -> POST http://roboco-agent-{agent_id}:{SDK_PORT}/turn. The container driver POSTs normalized StreamChunks to the relay push() endpoint. When the agent emits a roboco-meta fence, parse_readiness extracts ReadinessTag (covered/ready/scale) used by the orchestrator to decide proposal readiness. The CEO confirms via panel: confirm_live_draft (single) or confirm_live_batch (MegaTask) -> PrompterService.create_task_from_draft -> TaskService.create (DB). For a batch, _sequence_drafts (pure SequencingService.analyze) computes waves/edges, the umbrella is created branchless via _compose_umbrella_draft, N root-subtasks are created with BatchPlacement(parent=umbrella, batch_id, sequence=wave_index), then TaskService.add_dependency wires each edge (b depends on a). preview_batch returns the same waves without creating. update_live_draft applies board feedback to an existing task (update + approve_and_start or re-board). On board review, registry.park(session_id, task_id) keeps the chat alive; find_by_task recovers it for the re-draft injection. Idle sweep: orchestrator calls idle_session_ids(threshold) and close()s abandoned chats; close_by_agent fires on forced kill. Secretary: routes /api/secretary/* call read_company_state/read_task/submit_directive/confirm_directive/reject_directive -> SecretaryService -> TaskService/CompanyGoalsService/PitchService/MessagingService/NotificationService; gated kinds (UPDATE_CHARTER/CONTROL_TASK/APPROVE_PITCH/ANNOUNCE) persist PENDING + notify CEO, then confirm_directive runs _execute with the CEO as actor; RELAY_MESSAGE runs immediately.

## Mermaid
```mermaid
sequenceDiagram
  participant CEO
  participant Panel
  participant Orchestrator
  participant Reg as PrompterLiveRegistry
  participant Container as prompter container
  participant PS as PrompterService
  participant TS as TaskService
  CEO->>Orchestrator: start intake chat
  Orchestrator->>Reg: open(session_id, INTAKE_AGENT_ID)
  Panel->>Reg: stream(session_id) (SSE)
  CEO->>Panel: type message
  Panel->>Reg: deliver(session_id, text)
  Reg->>Container: POST /turn
  Container->>Reg: push(session_id, StreamChunk)
  Reg->>Panel: yield event
  CEO->>Panel: confirm draft (board/main_pm)
  Panel->>PS: confirm_live_draft(draft, agent_id, route)
  PS->>PS: create_task_from_draft
  PS->>TS: create(TaskCreateRequest, confirmed_by_human=True)
  TS-->>Panel: task_id
  alt MegaTask
    Panel->>PS: confirm_live_batch(title, drafts, project_ids, route)
    PS->>PS: _sequence_drafts -> waves + edges
    PS->>TS: create umbrella (branchless)
    loop each draft
      PS->>TS: create root-subtask (BatchPlacement)
    end
    loop each edge (a,b)
      PS->>TS: add_dependency(b, a)
    end
  end
  Orchestrator->>Reg: park(session_id, task_id) (board review)
  Orchestrator->>Reg: idle_session_ids(threshold) -> close() abandoned
```

```mermaid
stateDiagram-v2
  direction LR
  [*] --> Pending: submit_directive (gated)
  Pending --> Executed: confirm_directive (_run ok)
  Pending --> Rejected: reject_directive
  Pending --> Failed: _run raised domain error
  [*] --> Executed: submit_directive (RELAY_MESSAGE, direct)
  Executed --> [*]
  Rejected --> [*]
  Failed --> [*]
```

## Logical Tree
```
intake-secretary
  PrompterService (roboco/services/prompter.py)
    Draft validation & coercion
      _validate_and_coerce_draft
      _validate_draft_target (project/product/cell-map/umbrella)
      _coerce_draft_enums (team/task_type/nature/complexity)
      _coerce_priority
      _resolve_uuid_field
      _resolve_draft_assignee
    Team routing
      _resolve_owning_team
      _assignee_is_board
      _lead_cell_team
    Task creation
      create_task_from_draft (single + placement)
      confirm_live_draft (board / main_pm route)
      update_live_draft (re-draft in place)
    MegaTask batch
      _sequence_drafts -> SequencingService.analyze
      preview_batch (no-create preview)
      _validate_batch_scope (>=2 distinct projects, in-scope)
      confirm_live_batch (umbrella + N root-subtasks + edges)
      _compose_umbrella_draft
    Pure helpers
      parse_readiness, compose_description, format_board_briefing, compose_redraft_message
      _as_work_entry, _cell_teams, _draft_cell_map, derive_scale, _clean_list, _text, _bullets, _cell_label, _render_work_entry, _render_the_work, _section
    Dataclasses: ReadinessTag, BatchPlacement
  PrompterLiveRegistry (roboco/services/prompter_live.py)
    LiveIntakeSession dataclass (queue, closed, task_id, last_activity)
    Lifecycle: open, get, is_alive, close, close_by_agent, park, find_by_task
    Agent->panel: push, stream, idle_session_ids
    Panel->agent: deliver (POST /turn)
    Singleton: _RegistryHolder, get_live_registry
  SecretaryService (roboco/services/secretary.py)
    Reads: read_company_state, read_task
    Directives: get_directive, list_directives, submit_directive, confirm_directive, reject_directive, to_dict
    Internals: _pending_or_raise, _validate_payload, _run, _execute, _control_task, _notify_ceo_pending
```

## Dependencies
- Internal: roboco.services.task.get_task_service / TaskService, roboco.services.sequencing.SequencingService, roboco.services.company_goals.get_company_goals_service, roboco.services.messaging.get_messaging_service, roboco.services.pitch.get_pitch_service, roboco.services.notification.NotificationService, roboco.services.base.BaseService/NotFoundError/ConflictError/ValidationError/ServiceError, roboco.db.tables.AgentTable/TaskTable/SecretaryDirectiveTable, roboco.foundation.identity.CELL_TEAMS/AGENTS/role_for_uuid_or_none, roboco.foundation.policy.batch.is_batch_umbrella/main_pm_cannot_own_code/pm_cannot_own_code, roboco.foundation.policy.content.validators.coerce_str_list, roboco.foundation.policy.sequencing.models.DraftSurface/SequencePlan/SequencingError, roboco.foundation.policy.lifecycle (TaskStatus source), roboco.models.base (AgentRole/Complexity/TaskNature/TaskStatus/TaskType/Team), roboco.models.product.ProductCellMapping, roboco.models.task.TaskCreateRequest, roboco.models.secretary (DirectiveKind/DirectiveStatus/GATED_KINDS), roboco.seeds.initial_data.AGENT_UUIDS, roboco.utils.converters.require_uuid
- External: sqlalchemy (select, AsyncSession), structlog, httpx, asyncio, contextlib, json, re, dataclasses, uuid, datetime

## Entry Points

| Name | File | Trigger |
|---|---|---|
| POST /api/prompter/live/{session}/confirm-draft (confirm_live_draft) | roboco/api/routes/prompter_live.py | panel confirm button -> PrompterService.confirm_live_draft |
| POST /api/prompter/live/{session}/confirm-batch (confirm_live_batch) | roboco/api/routes/prompter_live.py | panel MegaTask confirm -> PrompterService.confirm_live_batch |
| GET /api/prompter/live/preview-batch (preview_batch) | roboco/api/routes/prompter_live.py | panel pre-confirm preview -> PrompterService.preview_batch |
| POST /api/prompter/live/{session}/redraft (update_live_draft) | roboco/api/routes/prompter_live.py | panel re-draft confirm -> PrompterService.update_live_draft |
| relay push/stream/deliver/is_alive endpoints | roboco/api/routes/prompter_live.py + secretary_live.py | panel SSE + message POST over PrompterLiveRegistry |
| orchestrator live-intake spawn/reap/idle hooks | roboco/runtime/orchestrator.py | _spawn_intake_container / _spawn_secretary_container / idle-reap sweep / board-review park / close_by_agent on kill |
| POST /api/secretary/state, /task, /directive, /directive/{id}/confirm\|reject | roboco/api/routes/secretary.py | Secretary panel surface -> SecretaryService reads + directive lifecycle |

## Config Flags
- ROBOCO_WORKSPACE_AUTO_CLONE / ROBOCO_WORKSPACE_CLONE_TIMEOUT (intake multi-repo clone scope: _clone_intake_scope, indirectly via orchestrator)
- ROBOCO_SELF_HEAL_ORIGINATE_ENABLED etc. do NOT gate this slice
- No direct ROBOCO_* flag in these three files; intake is a core capability (not feature-flagged), secretary is always-on; MegaTask is additive core, not gated


## Gotchas
- PrompterLiveRegistry.open is deliberately idempotent: a second open for an un-closed session returns the existing one instead of swapping the queue, because stream() captures the queue once and a fresh queue would strand the browser SSE on the old one while events push to the new one.
- Registry is a process-wide singleton held on _RegistryHolder (not a `global`); orchestrator is single-process and holds container state in memory — the relay is in-process only, not cross-process.
- deliver() logs transient POST failures at DEBUG (not ERROR) because the opening-message delivery retries until the container receiver is up; callers surface real failure (the /messages route 404s, _deliver_when_ready warns once after N tries).
- park() keeps a session alive (opposite of close) so board feedback can be injected in-context for an in-place re-draft; idle_session_ids explicitly excludes task_id-set (parked) sessions from idle reaping.
- TaskService is imported lazily inside create_task_from_draft / confirm_live_batch / update_live_draft to avoid circular imports.
- PM + code is structurally impossible: intake coerces code->planning via `_coerce_pm_code_to_planning`, which has two layers — team-based (main_pm_cannot_own_code) and assignee-based (pm_cannot_own_code for any PM assignee on a cell team). The umbrella is task_type=planning. TaskService.create is the backstop for non-intake HTTP paths.
- AGENTS['ceo'].uuid is captured at import time as _CEO_ID in secretary.py — CEO identity is a fixed seed uuid, not a DB lookup.
- _draft_cell_map de-dupes by team (first mapping wins) because task_cell_projects is unique per (task, team); a second the_work entry for the same cell is silently dropped.
- _compose_umbrella_draft produces a draft with NO project_id/product_id (branchless); _validate_draft_target's umbrella branch hard-rejects any target on it.
- Secretary _run catches ConflictError/NotFoundError/ValidationError/ValueError/KeyError -> FAILED with `error: {exc}` in result; any other exception propagates (no rollback of the flush).
- GATED_KINDS = {UPDATE_CHARTER, CONTROL_TASK, APPROVE_PITCH, ANNOUNCE}; only RELAY_MESSAGE runs immediately on submit_directive — ANNOUNCE is gated (needs CEO confirm), despite being a 'post a message' shape.
- compose_description falls back to the raw model description if the composed body is < _MIN_DESCRIPTION_LEN (20) chars — so a too-sparse structured draft still clears the schema minimum.


## Changes Since Baseline

| SHA | Subject | Impact |
|---|---|---|
| 15effce0 | feat(megatask): per-cell project map root-subtasks (multi-project, multi-cell) + main_pm+code impossibility + re-draft/batch hardening | Only commit touching this slice since baseline (prompter.py +228/-55; prompter_live.py and secretary.py unchanged). Adds the ad-hoc per-cell project map as a third draft target shape: _draft_cell_map, _MULTI_CELL_MIN, has_cell_projects param on _validate_draft_target, cell_projects on TaskCreateRequest, _validate_batch_scope counting per-cell pids. Adds main_pm_cannot_own_code coercion (code->planning) and switches umbrella task_type CODE->PLANNING. Extracts _validate_and_coerce_draft (coerces list fields via coerce_str_list) and _resolve_draft_assignee. Adds _as_work_entry to tolerate bare-string the_work entries. Changes _clean_list to use coerce_str_list (extracts dict-wrapped text instead of str(dict)). |

> Post-snapshot updates (since 2026-06-29): 536bbb64 (Chore/all/logical gaps sweep, PR#286, 2026-06-30) touched prompter.py only (prompter_live.py and secretary.py still unchanged). Key changes: (1) fixes Risk #1 — 1-cell map branch now conditioned on `resolved_project_id is None and resolved_product_id is None` so a top-level target is no longer silently dropped; (2) fixes Risk #2 — `_draft_cell_map` now raises `ValidationError` on a malformed project_id instead of silently continuing; (3) fixes Risk #4 — `create_task_from_draft` calls `_copy_draft` first so `_validate_and_coerce_draft` never mutates the caller's dict; (4) fixes Risk #5 — product/board routing is now checked BEFORE the multi-cell map force (multi-cell is inside the `if resolved_product_id is None:` branch); (5) extracts code->planning coercion into `_coerce_pm_code_to_planning`, extending it to cover PM assignees on any team (via the new `pm_cannot_own_code` helper imported from `roboco.foundation.policy.batch`); (6) adds `_copy_draft` module-level function. LOC grew from ~1066 to 1142.

## Regression Risks

| Title | File:Line | Claim | Severity |
|---|---|---|---|
| ~~1-cell map silently drops product_id and top-level project_id~~ **RESOLVED 536bbb64** | roboco/services/prompter.py:346 | ~~When _draft_cell_map returns exactly 1 entry, create_task_from_draft overwrites resolved_project_id with cell_map[0][1] and forces resolved_product_id=None.~~ Fixed: the 1-cell branch is now guarded by `resolved_project_id is None and resolved_product_id is None`; a top-level target is preserved over a redundant 1-cell map. | ~~medium~~ fixed |
| ~~Invalid project_id in a multi-cell map silently collapses the shape~~ **RESOLVED 536bbb64** | roboco/services/prompter.py:926 | ~~_draft_cell_map skips any the_work entry whose project_id fails UUID(str(pid)) (try/except continues).~~ Fixed: _draft_cell_map now raises `ValidationError` (clean 400) for a present-but-malformed project_id instead of silently continuing; the human is prompted to re-enter it. | ~~medium~~ fixed |
| Umbrella target gate is a behavior tightening that could reject previously-tolerated drafts | roboco/services/prompter.py:139 | The rewritten _validate_draft_target now hard-rejects an umbrella (is_batch_umbrella) that carries ANY target (project/product/cell-map). Before this commit an umbrella with only a project_id (no product_id) would not raise. Internal _compose_umbrella_draft never sets a project_id so the happy path is safe, but any external caller that builds a BatchPlacement(is_umbrella=True) draft with a stray project_id now gets a ValidationError instead of silent acceptance. | low |
| _clean_list semantics changed: dict-wrapped items now extracted instead of str(dict) | roboco/services/prompter.py:887 | _clean_list now delegates to coerce_str_list, which extracts text from dict-wrapped items (e.g. {'$text': ...}) instead of rendering `str(dict)`. This changes the rendered description text for any draft whose list fields contain dict-wrapped items. If coerce_str_list returns an unexpected shape for a non-string non-dict item (e.g. a list-of-lists), the rendered bullets / intended_to_touch / batch-scope counting could differ from the prior behavior. | low |
| ~~_validate_and_coerce_draft mutates the caller's draft dict in place~~ **RESOLVED 536bbb64** | roboco/services/prompter.py:207 | ~~_validate_and_coerce_draft overwrites draft_data fields in place; create_task_from_draft and confirm_live_batch callers were guarded by dict() copies but update_live_draft was not.~~ Fixed: create_task_from_draft now calls `_copy_draft(draft_data)` first (deep-copies the_work unit dicts too); the remaining concern for update_live_draft (no _validate_and_coerce_draft call) is unchanged. | ~~medium~~ partially fixed |
| ~~Multi-cell map team routing precedes product/board routing~~ **RESOLVED 536bbb64** | roboco/services/prompter.py:163 | ~~_resolve_owning_team checked multi-cell before product/board.~~ Fixed: product/board routing is now checked first (`if resolved_product_id is None:` gates the multi-cell path); a product draft with a ≥2-cell the_work map stays on the board-review path as required. The representation limit (product + cell-map not simultaneously expressible) is intentional, not a bug. | ~~low~~ fixed |

## Health
The slice is coherent and well-defended. prompter_live.py and secretary.py are unchanged since baseline and read as clean, focused singletons/services with correct lifecycle semantics (idempotent open, sentinel-based stream close, park-vs-close distinction, gated-vs-direct directive split backed by GATED_KINDS). The one changed file, prompter.py, gained the per-cell MegaTask map shape and the main_pm+code->planning coercion that closes the 2026-06-27 meltdown class; its validation is stricter and coercion is robust against LLM-emitted shapes (bare-string the_work, dict-wrapped list items, word-valued priority). The main integrity concerns are two silent-collapse paths in the new cell-map handling: a 1-cell map silently drops product_id/top-level project_id, and a malformed project_id in a multi-cell map silently collapses the shape to single-cell — neither raises, so an LLM producing a slightly-off draft will create a mis-shaped task instead of a clean 400. The update_live_draft path skips the new _validate_and_coerce_draft guard, so re-drafts are not protected against empty-after-coercion AC. No drift from CLAUDE.md was found; the MegaTask umbrella is branchless/planning, ANNOUNCE is gated, and single-task intake is preserved. Overall the slice is healthy but the silent-collapse edges warrant a hardening pass to convert them into ValidationErrors.
