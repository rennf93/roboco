# tg-approvals-tab.tsx: exhaustiveness guards for ApprovalItem switches

`panel/src/components/tg/tg-approvals-tab.tsx` renders one card stack over
the eight `ApprovalItem` kinds sourced by `use-approval-queue.ts`: `release`,
`x_post`, `video_post`, `roadmap`, `pest_control`, `spackle`, `dogfood`,
`scales`.

Three functions switch on `item.kind` and must all stay exhaustive whenever
a kind is added or removed: `itemTitle`, `itemSubtitle`, `Detail`. Missing a
case in any of them must fail `pnpm typecheck` — silently falling through to
`undefined` (an empty subtitle, or a `Detail` that renders nothing) is a bug
class this file guards against on purpose.

## Why itemTitle alone used to be safe

`itemTitle` returns a bare `string`. Panel's `tsconfig.json` runs strict mode
without `noImplicitReturns`, but a function whose return type is a concrete
type (not `string | undefined`) still fails to compile if control falls off
the end of a `switch` with no `return` — TypeScript can't prove every path
returns a `string`, so a missing case is already a compile error there.

`itemSubtitle` returns `string | undefined`, and the original `Detail` had
no return-type annotation (inferring `ReactElement | undefined`, which
React 19's `ReactNode` accepts for a component). In both cases, falling off
the end of the switch legally produces `undefined` — so a missing case for a
newly added kind used to compile clean and just render nothing, instead of
failing typecheck.

## The guard pattern

Both `itemSubtitle` and `Detail` now end with, immediately after the
`switch` and with no `default` arm:

```ts
const _exhaustive: never = item;
return _exhaustive;
```

Adding a ninth `ApprovalItem` kind without a matching `case` in either
function leaves `item` narrowed to that new kind's type at this point
rather than to `never`, so assigning it to the `never`-typed `_exhaustive`
fails typecheck. Do not add a `default` arm to any of the three
switches — a `default` would silently absorb a new kind instead of forcing
a compile error, defeating the guard.

When adding a ninth kind: add its case to `KIND_META`, `itemTitle`,
`itemSubtitle`, and `Detail`. Typecheck will fail at `itemSubtitle` and
`Detail` (via the `_exhaustive` guard) and at `itemTitle` (via the implicit
falls-off-the-end check) until all three are updated.
