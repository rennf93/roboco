"""The Entire trails bridge: RoboCo task trees mirrored onto Entire trails.

Behind ``entire_trails_enabled`` and a per-project opt-in, the dispatcher
mirrors each task tree (a coordination root, or a MegaTask umbrella) onto
one Entire trail: the root's intent and plan are the trail's text, every
branch a change, review findings trail findings, the CEO's approval a trail
approval. RoboCo's database stays canonical and its merge path the one that
merges; the mirror is one way. Agents never see Entire or its token.

- ``hooks``: outbox rows written in the lifecycle's own transactions.
- ``drain``: the orchestrator's loop runs them through the CLI; a branch's
  link before its first PR, and the CEO's approval before the merge, run
  inline.
- ``cli`` runs the pinned Entire CLI; ``render`` writes the text;
  ``outbox`` holds the operations and tree helpers; ``status`` reports the
  queue for the panel.

Callers import the submodule they need: the lifecycle imports ``hooks``
(light), never ``drain`` (which pulls in the CLI and its sandbox
dependencies).
"""
