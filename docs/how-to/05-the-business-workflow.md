# The business workflow

The first four chapters follow one task through the company. But a company isn't only its task queue — it has a direction, a budget, and a reason every task exists. That layer lives in the **Business** tab, and it is how you steer the whole organization rather than one job at a time.

## The charter

The charter is the company's standing context: its **north star**, its **objectives**, the **constraints** it must respect, and the **operating policy** it works under. You write it once in the Business tab and revise it whenever the direction shifts. It isn't decoration — the charter is threaded into every agent's briefing, into the pitches the Board produces, and into what the Secretary is allowed to act on. Set it well and every agent inherits the same sense of what the company is for; leave it empty and they only know the task in front of them.

<!-- Screenshot to capture: the Business tab charter editor with the north-star / objectives / constraints / operating-policy fields filled in. -->

## The Cockpit

Where the charter says what the company *should* be doing, the Cockpit shows what it *is* doing — the company reflected back against its own charter. It surfaces spend against budget and the signals worth your attention: drift from the stated objectives, agents sitting idle, work that has been blocked too long. It is the one screen that answers "is the company on course?" without your having to read every task.

<!-- Screenshot to capture: the Cockpit with spend-vs-budget and the signals panel. -->

## The Company Scorecard

On the Business tab's Goals view sits the Company Scorecard — live performance against the charter in one card. Where the Cockpit surfaces signals to act on, the Scorecard is the one-glance answer to "how is the company actually doing?": what's been delivered, spend against budget, the median lead time from task to merge, and progress on the objectives you set. It is the company's vital signs, read off the same work the rest of the panel tracks.

## The Secretary

The Secretary is your conversational chief-of-staff. You chat with it the way you'd brief a human one — ask it where things stand, or dictate a change to the charter. What it never does is act on its own: every directive it derives from your instruction is **gated**, landing in a queue for your explicit confirmation before anything happens. It reads the whole company's state to advise you, but it spends nothing, builds nothing, and approves nothing until you say the word. It is leverage with a safety catch — your intent, executed, but only after you confirm it.

<!-- Screenshot to capture: a Secretary chat dictating a charter tweak, and the resulting gated directive awaiting confirmation in the queue. -->

## Web research and the strategy engine

Two capabilities run above the day-to-day. Both are **off by default** and master-switched from **Settings → Feature Flags** in the panel — the switch persists and takes effect on the next backend restart. The environment variables below are the same toggles at the source, and still carry the parts the panel deliberately doesn't surface (the research provider and its API key, which never leave the server). Their effect shows up inside agent runs once enabled.

### Web research

Flip **Web research** on in Settings → Feature Flags (or set `ROBOCO_RESEARCH_ENABLED=true`), choose a provider with `ROBOCO_RESEARCH_PROVIDER` (`tavily`, `brave`, or `exa`), and supply `ROBOCO_RESEARCH_API_KEY`. With that in place, the Board and PM agents gain `web_search` and `web_fetch` through the `roboco-search` MCP server — so a Product Owner scoping a feature can ground it in the live web, not just your codebase. The API key stays server-side; the agents never see it and never make the external call themselves. Leave it off and the tools simply aren't there — a no-op.

### The strategy engine

Flip **the strategy engine** on in Settings → Feature Flags (or set `ROBOCO_STRATEGY_ENGINE_ENABLED=true`) and a background loop begins watching the company against its charter. When it spots drift from the objectives, agents gone idle, or work blocked for too long, it tells you. It is **notify-only by design**: it never spends, never builds, never approves — it raises the flag and leaves the decision where every decision belongs, with you. Off, it is fully dormant.

### The self-healing CI loop

The same shape, pointed inward: flip **self-healing** on in Settings → Feature Flags (or set `ROBOCO_SELF_HEAL_ENABLED=true`) and RoboCo begins watching its **own** repository's CI. When a run regresses it tells you. Turn on the second switch (`ROBOCO_SELF_HEAL_ORIGINATE_ENABLED=true`) and it goes one step further — it opens a fix task for the regression, but only as far as **PENDING, awaiting your approval**. It never starts, merges, or deploys that work itself: the company can notice it broke its own build and queue the repair, but the call to run it stays yours. Both switches are off by default, and it watches only the one repo you name as RoboCo itself.

## Feel the whole thing

The cleanest way to understand this layer is to walk it once, end to end:

1. Open the **Business** tab and set the charter — north star, a couple of objectives, your constraints, the operating policy.
2. Open the **Cockpit** and watch the company reflected against it — spend against budget, and the signals as they appear.
3. Chat the **Secretary** and dictate one change to the charter. Watch the gated directive land in the queue, and confirm it — that round trip, from your sentence to a confirmed action, is the whole shape of how you steer RoboCo from above.

---

Previous: **[← The last call — and the loop](04-the-last-call-and-the-loop.md)** · Back to **[the index](README.md)**

*RoboCo is early-stage, work-in-progress software (v0) — expect rough edges. The [Get Started guide](../get-started/index.md) covers setup, configuration, and the security model.*
