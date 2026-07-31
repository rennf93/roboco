# Backend Documentation

Documentation for the Backend Cell team.

## Access

- **READ**: Backend Cell (developers, QA, PM, documenter)
- **WRITE**: be-doc only

## Contents

- `/api/` - API documentation
- `/qa/` - QA-related docs
- `/services/` - Internal service architecture & patterns
  - `coordination-events.md` - 5 coordination-event notification producers: reassignment, collision-sequencing, unblock, dependency-revival, stale-claim-reaped
  - `evidence-assembly-timeout-fix.md` - claim_review/evidence()/roboco_git_diff timeout fix: dedup'd git.diff_and_files(), parallelized DB reads, bounded conventions-validator + evidence-assembly timeouts, structured gateway_timeout errors
- `/ops/` - Operational runbooks
  - `codeql-workflows.md` - Split CodeQL workflow triggers and branch-protection notes

## Contributing

Backend team members should request documentation updates through the Cell PM. Only the Backend Documenter (be-doc) can write to this directory.
