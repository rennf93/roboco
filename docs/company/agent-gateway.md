# How agents are sandboxed

The most important thing to understand about trusting RoboCo with your repository: **agents never touch your API, your database, or a shell directly.** Every action an agent can take goes through a narrow, server-side gateway that only exposes the handful of verbs that agent's *role* is allowed to use. Capability is decided by role at spawn time — not by the model's good behavior.

## Agents speak in verbs, not API calls

Each agent container talks to RoboCo through two thin MCP servers, both backed by a single server-side component (the **Choreographer**) that composes the real services behind the scenes:

| Server | What it exposes |
|--------|-----------------|
| `roboco-flow` | **Intent verbs** — the lifecycle actions: `give_me_work`, `i_will_work_on`, `open_pr`, `i_am_done`, `claim_review`, `pass_review`, `complete`, `submit_up`, `submit_root`, `pr_pass`, … |
| `roboco-do` | **Content tools** — `commit`, `note`, `say`, `dm`, `evidence`. |

Two more read-only servers give agents a read-only view of git (`status`, `log`, `diff`) and access to the knowledge base. That's the entire surface. There is no "run arbitrary SQL," no "call any endpoint," no general shell.

## A role can only call its own verbs

At spawn, every agent is handed a **manifest** listing exactly the verbs its role may call — and nothing else. The manifest is built from a server-side role configuration and mounted read-only into the container. The result is that the lifecycle's role rules aren't just policy, they're *unreachable code* for the wrong role:

- A **developer** can `give_me_work`, open a PR, and mark itself done — but there is no merge verb in its manifest.
- **QA** can claim a review and pass or fail it — but it has no `commit`.
- A **PR reviewer** can pass or fail an assembled PR and post its review on the PR — but it never gets agent chat verbs.
- The **Auditor** is restricted to leaving a private note and reading evidence; it cannot `say` or `dm`. It observes; it does not participate.

So when [the lifecycle](task-lifecycle.md#role-gated-transitions) says "only QA can pass QA" or "only the CEO merges to master," that boundary is enforced at the gateway: the verb simply isn't available to anyone else.

## Every action returns a structured envelope

Agents don't guess at state. Every verb returns a standardized **envelope**:

- On success: `{ status, task_id, next, evidence?, context_briefing }` — where `next` tells the agent what to call next.
- On error: `{ error, message, remediate, missing }` — where `remediate` tells the agent exactly how to fix it and retry.

That `next` / `remediate` contract is why agents move through the lifecycle reliably instead of flailing: the gateway leads them, step by step, and rejects anything out of order with an explanation rather than a crash.

## The other guardrails

A few more protections run by construction, the same way on every backend (Claude or Grok):

- **Claim-locking** serializes work, so two agents can't grab the same task or race a merge.
- **The token never enters the container.** Your GitHub PAT is injected only for the moment of a git operation, orchestrator-side, and scrubbed from every clone — see [Register a project](../get-started/first-project.md#what-happens-under-the-hood).
- **A prompt-injection guard** screens task prompts, and a bash guard blocks credential-exfiltration and identity-forgery patterns.
- **Rate limits and overloads park, they don't crash-loop.** If a provider returns a 429 or a persistent overload, RoboCo *queues* that agent's work and probes for recovery instead of burning tokens retrying. You'll see an amber banner; the work resumes automatically when the provider does.

The practical upshot for you as operator: the workforce is structurally constrained to do its job and only its job. You're not relying on twenty-five models all choosing to behave — you're relying on the fact that the misbehaving action isn't on the menu.

## Next

→ Watch it all in motion in [the Tour](../how-to/README.md), or head back to [the lifecycle](task-lifecycle.md).
