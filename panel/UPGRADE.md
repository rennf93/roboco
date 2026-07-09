# Panel Upgrade Guide

## Next.js Version Bumps

The RoboCo control panel runs on **Next.js 16** with **TypeScript** and **Tailwind CSS**. Upgrading Next.js requires careful attention to dependency alignment.

### Critical: eslint-config-next must track the same version as next

When you bump the `next` package, **always update `eslint-config-next` to match**. This package is Next.js's official ESLint configuration and must stay in sync with the version of Next.js itself.

**Example:** if you upgrade `next` from `16.1.1` to `16.2.6`, also upgrade `eslint-config-next` from `16.1.1` to `16.2.6`.

### Upgrade Procedure

1. **Update package.json** with the new version(s):
   ```json
   {
     "dependencies": {
       "next": "16.2.6"
     },
     "devDependencies": {
       "eslint-config-next": "16.2.6"
     }
   }
   ```

2. **Regenerate the lockfile without deleting node_modules:**
   ```bash
   cd panel
   pnpm install
   ```
This updates `pnpm-lock.yaml` while preserving `node_modules`, keeping the resolution deterministic.

3. **Run the quality gate:**
   ```bash
   cd panel
   pnpm lint
   pnpm typecheck
   pnpm test
   pnpm build
   ```
All must pass with no errors before committing.

4. **Commit the changes:**
   ```bash
   git add panel/package.json panel/pnpm-lock.yaml
   git commit -m "chore(panel): align dependencies to Next.js X.Y.Z"
   ```

### What Changed in 16.1.1 → 16.2.6

This bump included updates to:
- `@babel/parser`, `@babel/types`, `@babel/template`, `@babel/traverse` — minor version improvements
- `@babel/generator`, `@babel/helper-module-imports`, `@babel/helper-validator-identifier` — updated to handle edge cases
- `tinyglobby` — dependency used by ESLint, upgraded from 0.2.15 to 0.2.17
- `semver` — dev tooling dependency updated
- Removal of stale `@babel/*` and `@emnapi/*` package variants (transitive deduplication)

**No breaking changes to panel code were required** — the bump was purely dependency-graph alignment. If a future Next.js bump does require code changes (e.g., API deprecations), those will be noted by TypeScript or runtime errors during testing.

### Troubleshooting

- **If `pnpm install` fails:** Check that you have write permissions in `panel/` and that your `pnpm` version is up to date (`pnpm -v`).
- **If lint/typecheck/test fail after the bump:** Read the error messages carefully. They may point to:
  - Removed or deprecated ESLint rules (consult the Next.js changelog for your target version)
  - Type incompatibilities (update any TypeScript-related types)
  - Runtime incompatibilities (these are rare but need investigation)
- **If the build fails:** Check the Next.js release notes for your target version for any breaking changes to the build process.

### References

- [Next.js Releases](https://github.com/vercel/next.js/releases)
- [Next.js Upgrade Guide](https://nextjs.org/docs/upgrading)
- [ESLint Config for Next.js](https://www.npmjs.com/package/eslint-config-next)
