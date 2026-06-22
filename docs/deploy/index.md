# Configure & Deploy

The reference layer beneath [Get Started](../get-started/index.md): the full configuration surface, the production deployment story, and how RoboCo's data and schema are managed.

<div class="grid cards" markdown>

-   **[Deployment](deployment.md)**

    ---

    The compose files, the single-origin nginx, the host-path mounts, data persistence and backup, secure mode, and the startup sequence.

-   **[Environment reference](env-reference.md)**

    ---

    Every `ROBOCO_*` setting, by category, with its default and purpose — the canonical configuration reference.

-   **[Data & migrations](data-and-migrations.md)**

    ---

    The core data model, the pgvector requirement, and how the stack migrates itself on every boot.

-   **[Bootstrap & seeds](bootstrap-and-seeds.md)**

    ---

    What `make db-init` seeds — the agents, channels, and memberships a fresh company starts with.

</div>
