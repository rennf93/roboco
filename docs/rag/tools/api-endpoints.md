# API Endpoints Reference

Base URL: `http://{host}:{port}/api/v1`

## Tasks

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/tasks` | List tasks (filtered) |
| GET | `/tasks/my` | My assigned tasks |
| GET | `/tasks/pending` | Pending tasks |
| GET | `/tasks/blocked` | Blocked tasks |
| GET | `/tasks/awaiting-qa` | Tasks awaiting QA |
| GET | `/tasks/awaiting-docs` | Tasks awaiting docs |
| GET | `/tasks/awaiting-pm-review` | Tasks awaiting PM |
| GET | `/tasks/awaiting-ceo-approval` | Tasks for CEO |
| GET | `/tasks/team/{team}` | Tasks by team |
| GET | `/tasks/{id}` | Get task details |
| GET | `/tasks/{id}/subtasks` | Get subtasks |
| POST | `/tasks` | Create task |
| PATCH | `/tasks/{id}` | Update task |
| DELETE | `/tasks/{id}` | Delete task |
| POST | `/tasks/{id}/claim` | Claim task |
| POST | `/tasks/{id}/start` | Start work |
| POST | `/tasks/{id}/pause` | Pause work |
| POST | `/tasks/{id}/resume` | Resume work |
| POST | `/tasks/{id}/block` | Block on task |
| POST | `/tasks/{id}/soft-block` | Block on external |
| POST | `/tasks/{id}/unblock` | Unblock task |
| POST | `/tasks/{id}/verify` | Submit verification |
| POST | `/tasks/{id}/submit-qa` | Submit for QA |
| POST | `/tasks/{id}/pass-qa` | Pass QA |
| POST | `/tasks/{id}/fail-qa` | Fail QA |
| POST | `/tasks/{id}/docs-complete` | Complete docs |
| POST | `/tasks/{id}/submit-pm-review` | Submit to PM |
| POST | `/tasks/{id}/complete` | Complete task |
| POST | `/tasks/{id}/cancel` | Cancel task |
| POST | `/tasks/{id}/activate` | Activate task |
| POST | `/tasks/{id}/escalate` | Escalate task |
| POST | `/tasks/{id}/escalate-to-ceo` | Escalate to CEO |
| POST | `/tasks/{id}/ceo-approve` | CEO approve |
| POST | `/tasks/{id}/ceo-reject` | CEO reject |
| POST | `/tasks/{id}/substitute` | Substitute agent |
| POST | `/tasks/{id}/progress` | Update progress |

## Git

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/git/status` | Git status |
| GET | `/git/log` | Commit history |
| GET | `/git/branches` | List branches |
| GET | `/git/diff` | View diff |
| POST | `/git/commit` | Create commit |
| POST | `/git/push` | Push to remote |
| POST | `/git/branch/create` | Create branch |
| POST | `/git/checkout` | Checkout branch |
| POST | `/git/pr/create` | Create PR |
| POST | `/git/pr/merge` | Merge PR |

## Channels & Messages

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/channels` | List channels |
| GET | `/channels/{slug}/history` | Channel history |
| POST | `/messages` | Send message |
| GET | `/messages/{id}` | Get message |

## Notifications

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/notifications` | List notifications |
| GET | `/notifications/{id}` | Get notification |
| POST | `/notifications` | Send notification |
| POST | `/notifications/{id}/ack` | Acknowledge |

## Journals

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/journals/me/entries` | My entries |
| POST | `/journals/me/entries` | Create entry |
| GET | `/journals/me/stats` | My stats |
| GET | `/journals/{agent}/entries` | Read team journal |

## Knowledge Base

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/optimal/search` | Semantic search |
| POST | `/optimal/query` | RAG query |
| POST | `/optimal/mentor/ask` | Ask mentor |
| GET | `/optimal/stats` | KB stats |
| POST | `/optimal/index/code` | Index code |
| POST | `/optimal/index/docs` | Index docs |

## Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/health/ready` | Readiness check |
