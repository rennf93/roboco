# Choosing & Running Models

RoboCo's agents are backend-agnostic. By default the whole workforce runs on **Anthropic Claude**, authenticated from the Claude Code session on your host — no API key to manage. You can route the company onto **xAI Grok** instead, or point individual agents and roles at **local / self-hosted** models, all from one page in the panel.

<div class="grid cards" markdown>

-   **[Provider routing](provider-routing.md)**

    ---

    The Settings → AI Providers page: the routing modes, agent-over-role-over-global precedence, saved keys, and the fail-soft fallback.

-   **[Running on Grok](grok.md)**

    ---

    The whole workforce on xAI Grok via the official CLI and a SuperGrok subscription — no metered key.

-   **[Resilience](resilience.md)**

    ---

    What keeps a run alive: crash auto-retry, and how rate limits and provider overloads park work instead of dropping it.

</div>
