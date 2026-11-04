# Governance copy: one-idea-per-line prose vs the reflow gate

## Pattern

The one-idea-per-line brand cadence conflicts with the reflow gate, because `make quality` runs `scripts/reflow_md.py --check`, which rejects hard-wrapped prose in `README.md` and `docs/`. Write README and docs prose as one line per paragraph, or run `make reflow-docs` before committing. See `CONTRIBUTING.md` and `docs/rag/standards/markdown-reflow-quality-gate.md`.
