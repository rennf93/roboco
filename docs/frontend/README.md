# Frontend Documentation

Documentation for the Frontend Cell team.

## Access

- **READ**: Frontend Cell (developers, QA, PM, documenter)
- **WRITE**: fe-doc only

## Contents

- `/components/` - Component documentation
  - [Page-scoped refresh provider](./components/page-refresh-provider.md) — `PageRefreshProvider` callback registry that lets the navbar refresh button re-fetch only the current page.
  - [Project selector](./components/project-selector.md) — `ProjectSelector` dropdown for picking a project, with optional filtering by team and video-engine enablement.
- `/hooks/` - Hook documentation
- `/qa/` - QA-related docs

## Available docs

- [`a2a-filtering.md`](./a2a-filtering.md) — A2A filter bar, conversation list, and pulse-flash hook
- [`a2a-conversation-first-layout.md`](./a2a-conversation-first-layout.md) — Agent identity colors, connection states, context pane, transcript motion, and empty/error states
- [`hooks.md`](./hooks.md) — `usePageRefresh` and `PageRefreshProvider` usage and API reference
- [`forms/forms-audit.md`](./forms/forms-audit.md) — the living panel-forms ↔ backend-schema consistency audit (settings page + project + task dialogs); update the matching row in the same PR as any schema change
- [`forms/project-fields-audit.md`](./forms/project-fields-audit.md) — project dialog field reference (types, create-vs-edit exposure, the add-a-field checklist)

## Contributing

Frontend team members should request documentation updates through the Cell PM. Only the Frontend Documenter (fe-doc) can write to this directory.
