# Contributing to RoboCo

Thanks for your interest in contributing. This document explains the
contribution workflow and the licensing terms your contributions are made
under.

## License of contributions

RoboCo is licensed under the **GNU Affero General Public License v3.0**
(AGPL-3.0). Your contributions are accepted into the project under that
license.

## Contributor License Agreement (CLA)

Before your first pull request can be merged, you must sign the project's
[Contributor License Agreement](./CLA.md).

**Why a CLA?** The AGPL keeps the published project open. The CLA grants the
maintainer the additional rights needed to keep the project's future flexible
— for example, to offer a dual-licensed or commercial edition later, or to
re-license the codebase if that ever becomes necessary. Without it, the
project would be permanently locked to exactly one license, because each
contributor would retain sole copyright over their contribution.

Signing the CLA does **not** transfer ownership of your work away from you —
you keep your copyright. It grants the maintainer (and the maintainer's
successors and assigns) a broad license to use, distribute, and re-license
your contribution. See [`CLA.md`](./CLA.md) for the exact terms.

### How signing works

The first time you open a pull request, the CLA Assistant bot will comment with
a link and ask you to confirm agreement by posting a one-line comment on the
PR. This is a one-time action; subsequent PRs are recognized automatically.

## Development workflow

1. Fork the repository and create a feature branch.
2. Make your change. Follow the existing code style.
3. Run the full quality gate before opening a PR:

   ```bash
   make quality   # ruff format check, ruff check, mypy, pytest --cov-fail-under=80
   ```

   For the frontend (`panel/`):

   ```bash
   pnpm format && pnpm lint && pnpm typecheck && pnpm test
   ```

4. Sign your commits (recommended) with `git commit -s` to assert the
   Developer Certificate of Origin alongside the CLA.
5. Open a pull request with a clear description of the change and its
   motivation.

## Commit messages

Keep commits focused and descriptive. Do not include AI-generated attribution
footers or co-author trailers.

## Questions

Open a GitHub Discussion or issue if anything here is unclear.
