# Backend Documentation

Documentation for the Backend Cell team.

## Access

- **READ**: Backend Cell (developers, QA, PM, documenter)
- **WRITE**: be-doc only

## Contents

- `framework-comparison-2026.md` - Factual LangGraph / CrewAI / AutoGPT vs. RoboCo comparison mapping each framework's model to a real RoboCo mechanism (task lifecycle, role hierarchy, per-agent git isolation, PR-review gates, CEO-approval gate), with code citations
- `/api/` - API documentation
  - `x-post-response-schemas.md` - `XPostResponse` / `XPostHistoryResponse` Pydantic schemas and their source-specific ref fields (mention, feature, campaign, editorial, barfly) for the CEO X post queue and history view
- `/qa/` - QA-related docs
- `/services/` - Internal service architecture & patterns
  - `coordination-events.md` - 5 coordination-event notification producers: reassignment, collision-sequencing, unblock, dependency-revival, stale-claim-reaped
  - `pr-waiver.md` - Zero-diff PR-waiver: `submit_up`/`submit_root` detect a zero-commit branch before `create_pr`/`create_root_pr`, waive PR creation, and route report-only cell/root tasks to `completed` with no PR and no manual status surgery
- `/ops/` - Operational runbooks
  - `codeql-workflows.md` - Split CodeQL workflow triggers and branch-protection notes

## Contributing

Backend team members should request documentation updates through the Cell PM. Only the Backend Documenter (be-doc) can write to this directory.
