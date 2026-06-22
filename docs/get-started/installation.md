# Install & first run

This page takes you from nothing to the **Command Center** open in your browser. The whole stack runs in Docker, so the only things you install on the host are Docker itself and (for the default Claude backend) the Claude Code CLI.

## 1. Authenticate the model backend

By default every agent runs on Anthropic Claude, using the **Claude Code** session on your host rather than a metered API key. Authenticate once:

```bash
npm install -g @anthropic-ai/claude-code
claude   # opens a browser to log in; creates ~/.claude
```

That `~/.claude` directory is mounted read-only into the orchestrator, which hands it to each agent container. If you'd rather run the workforce on Grok, skip this and see [Optional: run on Grok](#optional-run-on-grok-instead) below.

## 2. Get the code

```bash
git clone https://github.com/rennf93/roboco.git
cd roboco
cp .env.example .env
```

## 3. Set the two required secrets

Open `.env` and set these two — the orchestrator refuses to start without the first, and Docker deployments need the second:

```bash
# Encrypts every per-project GitHub token at rest (Fernet). REQUIRED.
ROBOCO_ENCRYPTION_KEY=

# Signs the per-agent auth tokens (HMAC). REQUIRED for docker compose.
ROBOCO_AGENT_AUTH_SECRET=
```

Generate each one:

```bash
# ROBOCO_ENCRYPTION_KEY
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'

# ROBOCO_AGENT_AUTH_SECRET
python -c 'import secrets; print(secrets.token_hex(32))'
```

!!! danger "Keep `ROBOCO_ENCRYPTION_KEY` safe and stable"
    This key encrypts the GitHub tokens you'll store per project. If you lose it or change it later, **every stored token becomes undecryptable** and must be re-entered. Back it up with your other secrets, and never commit `.env`.

Everything else in `.env.example` has a working default for a local Docker run. On a NAS or remote server you'll also set the host-path variables (`ROBOCO_HOST_PROJECT_DIR`, `ROBOCO_HOST_CLAUDE_DIR`, `ROBOCO_DATA_DIR`) — those are covered in the deployment reference.

## 4. Bring up the stack

You have two ways to get the images. Both start from the clone above.

=== "Pre-built images (quickest)"

    Every release publishes all RoboCo images to both the GitHub Container Registry and Docker Hub, so you can run the full stack without building anything:

    ```bash
    docker compose -f docker-compose.registry.yml pull
    docker compose -f docker-compose.registry.yml up -d
    ```

    Pick the registry and version with two variables (defaults shown):

    ```bash
    ROBOCO_REGISTRY=ghcr.io/rennf93   # or docker.io/renzof93
    ROBOCO_VERSION=latest             # or a pinned release, e.g. 0.8.0
    ```

    The orchestrator pulls and spawns the matching pre-built agent images on demand — no build toolchain on your host.

=== "Build from source"

    The same stack, built locally from the Dockerfiles instead of pulled:

    ```bash
    docker compose up -d   # builds images on first run, then starts everything
    ```

The first start does real work: it pulls the local models into Ollama (the embedding model is quick; the local LLM is a couple of minutes), then the orchestrator waits for them, **runs the database migrations itself**, and indexes its knowledge base. You don't run migrations by hand — the stack brings its own schema up to date on every boot.

## 5. Watch it come up

```bash
docker compose logs -f orchestrator
```

Wait until the orchestrator reports it's serving, then open:

```text
http://localhost:3000
```

That's the **Command Center** — per-cell health, the CEO approval queue, live metrics, and recent activity. The company is up; it's just idle because you haven't given it a repository or a task yet.

!!! note "One address for everything"
    nginx is the only externally-exposed service. It serves the panel and proxies `/api` and `/ws` to the orchestrator, so the browser sees a single origin at `:3000`. You generally won't hit the orchestrator's own port directly.

## Next

→ **[Register your first project](first-project.md)** — point RoboCo at a repository it's allowed to work on.

---

## Optional: run on Grok instead

RoboCo can run the entire workforce on **xAI Grok** using xAI's official `grok` CLI on a **SuperGrok subscription** — no metered API key, so a run can't stall mid-task on exhausted credits.

```bash
grok login                     # once, on the host — creates ~/.grok/auth.json
```

Then in `.env`:

```bash
ROBOCO_HOST_GROK_DIR=/home/youruser/.grok   # the real host ~/.grok to mount in
# ROBOCO_GROK_AGENT_IMAGE=roboco-agent-grok:latest
# ROBOCO_GROK_CLI_MODEL=grok-build
# ROBOCO_GROK_REASONING_EFFORT=        # low|medium|high|xhigh|max (empty = model default)
```

The orchestrator keeps the short-lived Grok token refreshed for you, so agents don't hang on an expired login. Which agents run on which backend is set on the **Settings → AI Providers** page — that, and the per-role model assignments, are covered in the models section.

## Optional: secure mode

On a trusted LAN, RoboCo runs in **header-trust mode** by default: requests identify the caller by role headers, with no token required. That's fine on a private network and is the intended setup.

If you need to harden it so an agent can't spoof another's role, set `ROBOCO_AGENT_AUTH_REQUIRED=true`, keep your `ROBOCO_AGENT_AUTH_SECRET`, and generate the panel's CEO token with `make panel-token` into `ROBOCO_PANEL_AGENT_TOKEN`. nginx injects that token so the panel keeps working without the browser holding the signing secret. The full security model is in the Configure & Deploy reference.

!!! warning
    Don't expose RoboCo to the public internet as-is. It's built to run on a trusted private network (homelab / LAN).

## Resources { #resources }

RoboCo is light at runtime. Agent containers are spawned on demand and torn down when their work is done, so you rarely have more than a handful live at once.

| At idle (full stack, no task running) | RAM |
|---------------------------------------|-----|
| Ollama (models loaded) | ~2.2 GB |
| Orchestrator | ~150 MB |
| Postgres / Panel / Redis / nginx | ~120 MB combined |

The whole standing stack idles around **~2.5 GB**, almost all of it Ollama. Under load — five agents working concurrently — the stack peaked around **~6.6 GB**; even at full-fleet peak you stay well under ~10 GB. **Storage is the larger footprint:** the agent images all build from a shared base layer, so on disk they cost far less than their nominal sizes summed. `docker system prune` reclaims old image versions, stopped containers, and build cache.

```bash
docker stats        # live RAM / CPU per running container
docker system df    # image / container / build-cache disk usage
```

## Running on the host (for hacking on RoboCo itself)

If you want to develop RoboCo's own code rather than just run it, you can run only the backing services in Docker and the app on your host. RoboCo's code needs **Python 3.13+** (`uv` fetches it if needed):

```bash
uv sync
docker compose up -d postgres redis ollama   # backing services only
uv run alembic upgrade head                    # migrate the database
uv run python -m roboco.cli                    # API + orchestrator
```
