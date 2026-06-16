# Contributing to RoboCo

Thanks for your interest in contributing. This document explains the contribution workflow and the licensing terms your contributions are made under.

## License of contributions

RoboCo is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0). Your contributions are accepted into the project under that license.

## Contributor License Agreement (CLA)

Before your first pull request can be merged, you must sign the project's [Contributor License Agreement](./CLA.md).

**Why a CLA?** The AGPL keeps the published project open. The CLA grants the maintainer the additional rights needed to keep the project's future flexible — for example, to offer a dual-licensed or commercial edition later, or to re-license the codebase if that ever becomes necessary. Without it, the project would be permanently locked to exactly one license, because each contributor would retain sole copyright over their contribution.

Signing the CLA does **not** transfer ownership of your work away from you — you keep your copyright. It grants the maintainer (and the maintainer's successors and assigns) a broad license to use, distribute, and re-license your contribution. See [`CLA.md`](./CLA.md) for the exact terms.

### How signing works

The first time you open a pull request, the CLA Assistant bot will comment with a link and ask you to confirm agreement by posting a one-line comment on the PR. This is a one-time action; subsequent PRs are recognized automatically.

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

4. **Sign your commits.** `master` requires *verified* signatures, so set up commit signing before you push — see [Signing your commits](#signing-your-commits).
5. Open a pull request with a clear description of the change and its motivation.

## Commit messages

Keep commits focused and descriptive. Do not include AI-generated attribution footers or co-author trailers.

## Signing your commits

`master` is protected by a rule that **every commit must carry a verified signature**. Set this up once and it's automatic from then on; otherwise a maintainer has to bypass the rule to merge your PR.

> This is *cryptographic* signing (`git commit -S`, shown as **Verified** on
> GitHub) — not the `-s` Developer Certificate of Origin *sign-off* trailer. The
> sign-off does **not** satisfy the signature rule.

The lowest-friction method reuses the SSH key you already use with GitHub:

```bash
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub   # or id_rsa.pub
git config --global commit.gpgsign true
```

Then add that **same public key** to GitHub a second time as a signing key: **Settings → SSH and GPG keys → New SSH key → Key type: _Signing Key_**. (GPG signing also works if you prefer it.)

Your next commit will be signed; confirm with `git log --show-signature -1`. If you already pushed **unsigned** commits on your PR, re-sign the whole branch and force-push:

```bash
git rebase --exec "git commit --amend --no-edit -S" origin/master
git push --force-with-lease
```

## Questions

Open a GitHub Discussion or issue if anything here is unclear.
