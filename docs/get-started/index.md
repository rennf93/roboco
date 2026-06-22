# Get Started

Three steps take you from a cloned repository to a company working on your code:

1. **[Install & first run](installation.md)** — bring up the whole stack with Docker, set two secrets, and open the Command Center.
2. **[Register your first project](first-project.md)** — point RoboCo at a git repository it's allowed to work on.
3. **[Your first task](first-task.md)** — hand the company something to build and watch it go end to end.

You don't need Python, Node, or a database on your machine — everything runs in Docker. The one thing you provide is a way for the agents to reach a model: by default that's the **Claude Code** session already on your host, so there's no API key to wire up.

!!! tip "Prefer to watch first?"
    The [26-minute intro](https://www.youtube.com/watch?v=t1QNqJgBmkM) and the [full panel walkthrough](../how-to/README.md) are the fastest way to get the shape of the thing before you install it.

## What you'll need

| You need | Why |
|----------|-----|
| **Docker** + **Docker Compose** | The entire stack (PostgreSQL, Redis, Ollama, the orchestrator, the panel, nginx) runs as containers. |
| **A Claude Code auth directory** (`~/.claude`) | Mounted into the orchestrator so agents can reach the model. Run `claude` once on the host to create it. *(Or run the workforce on [Grok](installation.md#optional-run-on-grok-instead) instead.)* |
| **A GitHub Personal Access Token** | One per project you register, so agents can clone it and open pull requests. You add this in the panel later, not now. |
| **~10 GB of disk** and a few GB of RAM | The image set and the per-agent git clones. RoboCo is light at runtime — see the [resource notes](installation.md#resources). |

## The shape of the system

Everything is served behind a single address — **`http://localhost:3000`** — by an nginx reverse proxy. The browser only ever sees one origin; nginx routes `/api` and `/ws` to the orchestrator (FastAPI) and everything else to the Next.js control panel. That panel is your one window into the company.

When you're set up and looking at the **Command Center**, head into [The Company](../company/index.md) to understand the org and the lifecycle, or take [the Tour](../how-to/README.md) to watch a real feature get built.
