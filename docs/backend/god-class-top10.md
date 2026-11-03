# god_class: top 10 files by method count

Read-only analysis of the `god_class` convention rule (a class with more than 15 methods,
`_GOD_CLASS_METHODS = 15` at `roboco/conventions/modularity.py:69`). No source, config or test
files were changed.

## How it was counted

The checker's own logic was replicated verbatim, because importing `roboco.*` from an agent shell is
blocked. For every git-tracked `*.py` file, each **top-level** class (plain or decorated, as in
`_py_class`) is counted the way `_py_method_count` counts it: the direct children of the class body
that are a `function_definition`, or a `decorated_definition` wrapping one. Nested classes and
functions nested inside methods are not counted. This matches the checker, which only inspects
top-level classes, so the numbers agree with what the gate reports. A file's rank is its worst
class's count. Each of the 42 offending files has exactly one offending class.

The overall figure here is 42 offending classes in 42 files. This is far below the 313 hits quoted in
the intake. The checker counts one hit per class per run, so the gap is most likely a hit total
accumulated across many runs or many changed-file scans, not distinct classes. It was not
investigated further.

## Top 10

| # | File | Worst class (line) | Methods | Class lines | Tag | Reason |
|---|------|--------------------|--------:|------------:|-----|--------|
| 1 | `roboco/runtime/orchestrator.py` | `AgentOrchestrator` (:1109) | 501 | ~16,900 | roadmap | Spawn, reaper, dispatch, sandbox and budget loops share state; splitting needs a designed seam, not a mechanical move. |
| 2 | `roboco/services/task.py` | `TaskService` (:1012) | 339 | ~11,250 | roadmap | Lifecycle transitions, guards, findings and briefings are tightly coupled through one session; needs a design for service boundaries. |
| 3 | `roboco/services/gateway/choreographer/_impl.py` | `Choreographer` (:409) | 209 | ~8,760 | roadmap | Every verb composes the same services and guards; the verb-runner contract needs design before decomposing. |
| 4 | `roboco/services/gateway/content_actions.py` | `ContentActions` (:756) | 172 | ~5,785 | Pest Control | Methods group by independent content tool (commit, note, dm, sandbox, video), so a split into per-tool modules is bounded and mechanical. |
| 5 | `roboco/services/git.py` | `GitService` (:391) | 163 | ~6,030 | roadmap | Forge routing, rebase/sync state machines and PR/CI logic interact; a split needs a decided ownership model. |
| 6 | `roboco/services/optimal.py` | `OptimalService` (:158) | 72 | ~1,930 | Pest Control | Indexing, query and RAG-answer method groups are separable behind the existing service facade. |
| 7 | `roboco/services/telegram_inbound.py` | `TelegramInboundEngine` (:456) | 59 | ~1,060 | Pest Control | Command and button handlers are independent per command and can move to handler modules one by one. |
| 8 | `roboco/services/a2a.py` | `A2AService` (:75) | 51 | ~2,025 | Pest Control | Send, read, authorization and CEO-thread helpers split cleanly into private helper modules. |
| 9 | `roboco/services/x_engine.py` | `XEngine` (:475) | 50 | ~1,080 | Pest Control | Release, mention and feature-spotlight drafting are three independent flows sharing only the voice guide. |
| 10 | `roboco/services/notification_delivery.py` | `NotificationDeliveryService` (:240) | 48 | ~1,675 | Pest Control | Channel delivery, outbox handling and the re-escalation sweep are separable, though the sweep's commit semantics must be preserved. |

`roboco/services/workspace.py` (`WorkspaceService`, 48 methods) ties with row 10 and would be the
11th entry. It also reads as a Pest Control candidate (clone, worktree and venv groups), but it was
not tagged in detail.

## Reading the tags

- **Pest Control:** a bounded, mostly mechanical split. Method groups are already independent and a
  facade can keep the public API stable.
- **roadmap:** needs a design decision about boundaries and shared state before any code moves.
  The top three are 8k to 17k line classes, where an ad hoc split risks regressions across the
  lifecycle.
