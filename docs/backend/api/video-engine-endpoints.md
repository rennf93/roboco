# Video Engine API: Project-Scoped Endpoints

## Overview

The RoboCo video engine API is CEO-only and manages three concerns:

1. **On-demand video requests**: `POST /api/video/request` opens a video-authoring task scoped to a specific project
2. **Re-render (CEO retry)**: `POST /api/video/pipeline/{task_id}/rerender` clears render idempotency keys to re-trigger rendering
3. **Live preview proxy**: `GET /api/video/preview/{task_id}/{file_path:path}` serves authoring task composition HTML + assets with path-traversal confinement

All endpoints are CEO-only and require the global video engine flag enabled (`ROBOCO_VIDEO_ENGINE_ENABLED`).

---

## Endpoint: POST /api/video/request

### Purpose

Open a UX/UI video-authoring task for the CEO's on-demand brief, scoped to a specific project.

### Authentication

CEO-only (401 if not CEO).

### Request Body (VideoRequestBody)

```json
{
  "occasion": "string",      // Unique identifier; required, min 1 char
  "brief": "string",         // Video brief description; required, min 1 char  
  "platforms": ["string"],   // Target platforms: ["x", "tiktok"]; required, min 1
  "project_id": "UUID"       // Project to author against; required (NEW in v2)
}
```

**Breaking Change**: `project_id` is now **required**. This field scopes the video authoring task and its render pass to the specified project, replacing the hardcoded `self_heal_project_slug` behavior.

### Response (VideoRequestResponse)

```json
{
  "status": "opened|disabled|not_opened",
  "task_id": "UUID|null",
  "detail": "string"
}
```

### Response Codes

| Code | Status | Meaning |
|------|--------|---------|
| 200 | opened | Task created and dispatched to UX/UI developer |
| 200 | disabled | Video engine is off (`ROBOCO_VIDEO_ENGINE_ENABLED=false`) |
| 200 | not_opened | Duplicate occasion or open-post cap reached |
| 404 | — | `project_id` unresolvable OR project not opted in (`video_engine_enabled=false`) |
| 401 | — | Not authenticated as CEO |

### Behavior

1. **Project validation**: Looks up `project_id` and checks `video_engine_enabled=true`. Returns 404 if unresolvable or not opted in.
2. **Task creation**: Opens a normal ASSIGNED delivery task (`source=video`) dispatched to an available UX/UI developer (balanced by open-task count).
3. **Duplicate check**: Returns `not_opened` if a task for this `occasion` is already open.
4. **Open-post cap**: Returns `not_opened` if open-task count ≥ `ROBOCO_VIDEO_MAX_OPEN_POSTS`.

### Example

```bash
curl -X POST http://localhost:3000/api/video/request \
  -H 'X-Agent-Token: <ceo-token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "occasion": "v2.0 Launch",
    "brief": "30-second teaser for new dashboard",
    "platforms": ["x", "tiktok"],
    "project_id": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

---

## Endpoint: POST /api/video/pipeline/{task_id}/rerender

### Purpose

Clear render idempotency keys (`render_status`, `render_attempts`, `render_error`) on a completed video-authoring task, triggering the render loop to re-pick it up on the next cycle.

**Use case**: CEO fixes a composition error or wants to retry past a `failed` terminal state.

### Authentication

CEO-only (401 if not CEO).

### Path Parameters

| Name | Type | Description |
|---|---|---|
| `task_id` | UUID | Video-authoring task ID |

### Response (VideoPipelineItemResponse)

```json
{
  "task_id": "UUID",
  "title": "string",
  "occasion": "string",
  "status": "string",
  "pr_number": "int|null",
  "composition_id": "string|null",
  "render_status": "string|null",
  "render_attempts": "int",
  "max_attempts": "int",
  "render_error": "string|null"
}
```

After clearing, `render_status`, `render_attempts`, and `render_error` are `null` or zero.

### Response Codes

| Code | Meaning |
|------|---------|
| 200 | Keys cleared; next render cycle re-picks this task |
| 404 | Task not found, not video task, not completed, or no `composition_id` (nothing to render) |
| 401 | Not authenticated as CEO |

### Behavior

1. **Validation**: Checks task exists, is a video-authoring task (`source=VIDEO_SOURCE`), is COMPLETED, and has a `composition_id`.
2. **Clear keys**: Removes `render_status`, `render_attempts`, `render_error` from `video_draft` marker; preserves other fields.
3. **Render loop pickup**: On next orchestrator cycle, render loop scans for tasks with `render_status` unset and re-renders.

### Example

```bash
curl -X POST http://localhost:3000/api/video/pipeline/550e8400-e29b-41d4-a716-446655440000/rerender \
  -H 'X-Agent-Token: <ceo-token>'
```

---

## Endpoint: GET /api/video/preview/{task_id}/{file_path:path}

### Purpose

Serve a video-authoring task's composition HTML and sibling assets (kit/, public/, etc.) from the project's merged read-clone. Used by the panel's live preview iframe.

### Authentication

CEO-only (401 if not CEO).

### Path Parameters

| Name | Type | Description |
|---|---|---|
| `task_id` | UUID | Video-authoring task ID |
| `file_path` | string | Path relative to workspace root; e.g., `motion/compositions/<id>/vertical.html` |

### Response

- **Content-Type**: Auto-detected from file extension
- **Headers**:
  - `X-Frame-Options: SAMEORIGIN` — Allows same-origin iframe embedding
  - `Content-Security-Policy: frame-ancestors 'self'` — Restricts frame embedding to same origin
- **Body**: File contents (HTML, CSS, JS, images, etc.)

### Response Codes

| Code | Meaning |
|------|---------|
| 200 | File served successfully |
| 404 | Task/project not found, file doesn't exist, or file path escapes workspace root |
| 401 | Not authenticated as CEO |

### Behavior

1. **Task lookup**: Fetches task; validates it's a video task (`source=VIDEO_SOURCE`) with `project_id`.
2. **Project resolution**: Looks up project by `project_id`.
3. **Workspace fetch**: Ensures project's read-clone is available (clones if needed).
4. **Path resolution**:
   - Strips leading `/` from `file_path`
   - Resolves against workspace root
   - Validates resolved path is under root and is a regular file
   - Returns 404 on traversal attempt or non-file path
5. **Serve**: Returns file with iframe-permitting headers.

### Path Traversal Confinement

The `_resolve_preview_path` helper prevents directory-traversal attacks:

```python
candidate = (root / file_path.lstrip("/")).resolve()
if not candidate.is_relative_to(root) or not candidate.is_file():
    return None
```

Guarantees:
- `../` sequences are resolved before confinement check
- Absolute paths don't escape (resolved relative to root)
- Symlinks are resolved and still held under confinement
- Only regular files served; directories return 404

### Example

```bash
# Serve composition HTML
curl -H 'X-Agent-Token: <ceo-token>' \
  'http://localhost:3000/api/video/preview/550e8400-e29b-41d4-a716-446655440000/motion/compositions/my-id/vertical.html'

# Serve referenced asset
curl -H 'X-Agent-Token: <ceo-token>' \
  'http://localhost:3000/api/video/preview/550e8400-e29b-41d4-a716-446655440000/kit/public/logo.png'
```

---

## Project-Scoping Architecture

### What Changed

Previously, the video engine hardcoded `settings.self_heal_project_slug` everywhere. Now:

1. **On-demand requests** (`POST /video/request`): `project_id` required in request body
2. **Authoring tasks**: Each task stores its own `project_id`
3. **Render loop** (`orchestrator._render_both_cuts`): Uses task's `project_id` to resolve motion/ workspace, not hardcoded setting

### Rationale

Each opted-in project can now:
- Author and render videos against its own `motion/` directory
- Participate in release and spotlight videos from its own codebase
- Support on-demand briefs scoped to specific projects

### Resolution Method

New `VideoEngine.resolve_authoring_project(project_id, occasion)`:

- **If `project_id` provided** (on-demand, per-task): Looks up project by ID
- **If `project_id` is None** (release/spotlight hooks): Falls back to fixed RoboCo project (`self_heal_project_slug`)
- **Both paths**: Check `video_engine_enabled` and log skip reasons identically

### Migration Impact

**For on-demand endpoint clients**:
- Must now supply `project_id` in request body
- Requests without `project_id` fail validation (422)
- Panel's video-request form needs project picker (frontend task, out of scope)

**For release/spotlight hooks**:
- No change; they continue defaulting to fixed RoboCo project when no `project_id` provided
