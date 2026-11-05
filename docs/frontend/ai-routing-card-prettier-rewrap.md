# AI routing card: CardDescription Prettier re-wrap

PR: https://github.com/rennf93/roboco/pull/1516 (task 1ce359f2)

## What changed

`panel/src/components/settings/ai-routing-card.tsx`: the `CardDescription` JSX text had a line of 81 characters, which failed the `panel-ci` Prettier check (80-column limit). Running the panel's own Prettier moved the word "mounted" onto its own line.

## Behavior

None. The rendered copy is identical; only source line breaks changed. No props, API, or styling changes, so there is no migration or user-facing documentation.

## Verification

`make panel-gate` passes (format:check, lint, typecheck, tests). The PR diff touches only the one file above.

## Note for maintainers

During this task an automatic "regenerate generated artifacts" commit repeatedly re-applied drift to 19 generated files (`agents/prompts/_generated/*`, `docs/rag/lifecycle/*`, `panel/lib/lifecycle.json`). The final head's `files_changed` lists only the tsx file; if the PR later shows more files, restore those generated files to base content.
