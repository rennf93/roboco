# API Reference

RoboCo is API-first: the control panel is just a client of the same REST and WebSocket surface you can drive yourself. This section is for integrators and the curious; the live, always-current schema is at **`/docs`** (Swagger UI) and **`/redoc`** on the orchestrator.

<div class="grid cards" markdown>

-   **[REST API](rest-api.md)**

    ---

    The `/api` domain routes, the agent gateway verbs, the error envelope, and where to find the live OpenAPI.

-   **[WebSocket streams](websockets.md)**

    ---

    The `/ws` live streams the panel consumes — per-resource feeds and the operator system stream.

-   **[Authentication](auth.md)**

    ---

    Header-trust mode versus secure token mode, and how the panel stays authenticated.

</div>
