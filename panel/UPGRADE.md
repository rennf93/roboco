# Panel Upgrade Guide

## Next.js Version Bumps

The RoboCo control panel runs on **Next.js 16** with **TypeScript** and **Tailwind CSS**. Upgrading Next.js requires careful attention to dependency alignment.

### Critical: eslint-config-next must track the same version as next

When you bump the `next` package, **always update `eslint-config-next` to match**. This package is Next.js's official ESLint configuration and must stay in sync with the version of Next.js itself.

**Example:** both are `16.4.3` today. If you upgrade `next` to `16.5.0`, upgrade `eslint-config-next` to `16.5.0` in the same commit.

### Upgrade Procedure

1. **Update package.json** with the new version(s):

   ```json
   {
     "dependencies": {
       "next": "16.4.3"
     },
     "devDependencies": {
       "eslint-config-next": "16.4.3"
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

### What Changed in eslint-config-next 16.2.6 → 16.4.3 (with ESLint 10, Vitest 5, jest-dom 7)

`eslint-config-next` had stayed at `16.2.6` while `next` moved to `16.4.3`; it now matches `next` again. The same change moved ESLint 9 → 10, Vitest and `@vitest/coverage-v8` 4 → 5, and `@testing-library/jest-dom` 6 → 7. What each one needed:

- **`eslint-plugin-react-hooks` 7.1** (pulled in by `eslint-config-next` 16.4.3) finds more ref writes during render (`react-hooks/refs`) and `setState` calls in effect bodies (`react-hooks/set-state-in-effect`). The six it found were fixed in the code: latest-value refs are written in an effect, prop-to-state syncs use a `prev…` state comparison during render, and the markdown checkboxes are located by a rehype plugin (each carries its list item's source offset) instead of a render-time counter.
- **ESLint 10:** `eslint-config-next` 16.4.3 depends on `eslint-plugin-react` 7.37.5, `eslint-plugin-import` 2.32.0 and `eslint-plugin-jsx-a11y` 6.10.2, the latest release of each, and their `peerDependencies` stop at ESLint 9, so `pnpm peers check` reports `eslint` as an unmet peer for those three. Their rules load and report under ESLint 10 and `pnpm lint` passes. Recheck when those plugins publish an ESLint 10 range.
- **TypeScript stays on 5.x.** typescript-eslint 8 accepts `typescript >=4.8.4 <6.1.0`, and Dependabot holds TypeScript majors (see `.github/dependabot.yml`).
- **Vitest 5 + jest-dom 7 matcher types:** Vitest 5 no longer reads custom matcher types from the global `jest.Matchers`, and jest-dom 7.0.1's `@testing-library/jest-dom/vitest` entry still targets Vitest 4's `Assertion<T>`. `src/test/setup.ts` registers the matchers and declares their types on Vitest 5's `Matchers<R, T>`; its comment says when to drop that.

### Troubleshooting

- **If `pnpm install` fails:** Check that you have write permissions in `panel/` and that `pnpm -v` prints the version `packageManager` pins in `package.json` (`pnpm@11.10.0`).
- **If lint/typecheck/test fail after the bump:** Read the error messages carefully. They may point to:
  - Removed or deprecated ESLint rules (consult the Next.js changelog for your target version)
  - Type incompatibilities (update any TypeScript-related types)
  - Runtime incompatibilities (these are rare but need investigation)
- **If the build fails:** Check the Next.js release notes for your target version for any breaking changes to the build process.

### References

- [Next.js Releases](https://github.com/vercel/next.js/releases)
- [Next.js Upgrade Guide](https://nextjs.org/docs/upgrading)
- [ESLint Config for Next.js](https://www.npmjs.com/package/eslint-config-next)
