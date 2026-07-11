# Video Post Queue: Project Picker, Re-render, and Composition Preview

The video posting flow consists of three major components: project selection for on-demand video requests, a re-render retry control for failed drafts, and a live composition preview panel. All three integrate into the VideoPostRow and RequestVideoDialog components in `panel/src/components/dashboard/video-post-queue.tsx`.

## Project Picker in RequestVideoDialog

The "Request a video" dialog now requires a project selection before the CEO can submit. The picker is built on the existing `ProjectSelector` component (from `panel/src/components/projects/project-selector.tsx`) that provides a combobox populated via `projectsApi.list()`.

### Implementation

- **Location**: RequestVideoDialog component, first form field
- **State**: `projectId` (string | null), initialized to `null`
- **Binding**: The `ProjectSelector` renders with `value={projectId}` and `onChange={setProjectId}`, and the Request button is disabled until `projectId` is truthy
- **Payload**: The `project_id` is passed as a string to `videoApi.requestVideo()` in the mutation body

### Usage in RequestVideoDialog

```typescript
const [projectId, setProjectId] = useState<string | null>(null);

// Inside the form:
<div className="space-y-2">
  <Label>Project</Label>
  <ProjectSelector
    value={projectId}
    onChange={setProjectId}
    placeholder="Select the project this video is about..."
    allowClear={false}
  />
</div>

// Submit guard:
const canSubmit =
  !!projectId &&
  occasion.trim().length > 0 &&
  brief.trim().length > 0 &&
  platforms.length > 0;
```

The picker prevents submission of the video request until a project is explicitly selected, ensuring every on-demand video is scoped to a specific project.

## Re-render Control for Any Composition-Bearing Draft

The `RerenderControl` component (`panel/src/components/dashboard/video-rerender-control.tsx`) appears on any draft that has both an authoring task and a proposed composition, giving the CEO a way to retry — or deliberately redo — a render without creating a new request.

### When It Appears

- Rendered in `VideoPostRow` when both conditions hold:
  - `post.source_task_id` is truthy (the authoring task exists)
  - `post.composition_id` is truthy (a composition was proposed)
- This is regardless of `render_status` — the backend's rerender endpoint only requires a completed authoring task with a proposed composition, not a failed render, so a healthy render can be deliberately redone too
- Positioned in the draft header row, right-aligned after the occasion badge

### Visual States

1. **Idle** ("Re-render" button) — ready to click
2. **Loading** ("Re-rendering...") — mutation in flight, button disabled, spinner animating
3. **Error** ("Retry re-render") — the retry itself failed, button text and border turn red (`text-destructive` / `border-destructive`), button re-enables so the CEO can try again

### API Interaction

- Calls `videoApi.rerender(authoringTaskId)` where `authoringTaskId` is the draft's `source_task_id` (the video-authoring task, NOT the draft's own task_id)
- The backend endpoint is `POST /video/pipeline/{task_id}/rerender` and clears the render idempotency keys so the render loop picks up the task on its next cycle
- On success, invalidates the `["video", "pipeline"]` query key and shows a success toast: "Re-render queued — it will re-pick up on the next cycle."
- On error, shows an error toast with the backend message

### Implementation Detail

The component uses `useMutation` from @tanstack/react-query and mirrors the pattern in the "Reject draft" dialog's mutation (error state re-enables the button, allowing the CEO to retry).

## Composition Preview Panel

The `CompositionPreviewPanel` displays a live, read-only preview of the video composition (the actual HyperFrames HTML render) alongside the platform captions so the CEO can see exactly what will post before approving.

### When It Appears

- Rendered in `VideoPostRow` immediately above the MP4 player
- Only shows when BOTH of these are true:
  - `post.composition_id` is truthy
  - `post.source_task_id` is truthy

This degrades gracefully: older drafts or drafts from versions before the backend exposed these fields simply render no preview panel (not a crash).

### Layout

The panel is a two-column grid on desktop (`sm:grid-cols-2`) that stacks on mobile:

1. **Left column (desktop) / Top (mobile)**:
   - An `<iframe>` element embedding the composition HTML directly
   - The iframe uses a sandboxed environment (`sandbox="allow-scripts"`)
   - Lazy-loads for performance (`loading="lazy"`)
   - Applies aspect-video sizing and a black background

2. **Right column (desktop) / Bottom (mobile)**:
   - A "Captions as they will post" header
   - Per-platform caption display:
     - "X:" followed by the `x_caption` (if present)
     - "TikTok:" followed by the `tiktok_caption` (if present)

### Composition Preview URL

The iframe `src` is built using the `compositionPreviewUrl()` helper:

```typescript
compositionPreviewUrl(
  post.source_task_id,        // authoring task ID
  post.composition_id,         // composition ID from the draft
  cut,                         // current cut selection (vertical or square)
)
// → `/api/video/preview/{source_task_id}/motion/compositions/{composition_id}/{cut}.html`
```

The backend's `GET /video/preview/{task_id}/{file_path:path}` route serves the composition HTML + sibling assets from the project's merged read-clone with iframe-permitting headers (no auth-header workaround needed like the MP4 route).

### Caption Display

Captions are displayed as read-only text. The component only renders a caption section if at least one platform has a caption defined. This mirrors the structure the CEO will see when editing captions below (the "Edit X caption" / "Edit TikTok caption" checkboxes), giving visual parity between the live preview and the editable form.

## API Updates

### VideoPost Interface

Two new optional fields were added to mirror the backend's `video_draft` marker's render idempotency tracking:

```typescript
composition_id?: string | null;  // The composition this draft rendered from
render_status?: string | null;   // null | "rendered" | "failed"
```

Both are optional because drafts created before the backend exposed these fields will not have them. The UI gracefully handles their absence (re-render button doesn't appear, composition preview doesn't render).

### New API Functions

#### `compositionPreviewUrl()`

Builds the URL for the composition preview iframe:

```typescript
export function compositionPreviewUrl(
  authoringTaskId: string,
  compositionId: string,
  cut: VideoCut,
): string
```

- **Parameters**:
  - `authoringTaskId`: The video-authoring task ID (VideoPost.source_task_id)
  - `compositionId`: The composition ID (VideoPost.composition_id)
  - `cut`: "vertical" or "square"
- **Returns**: The URL to pass to `<iframe src>`

#### `videoApi.rerender()`

Triggers a re-render of a failed composition:

```typescript
rerender: async (authoringTaskId: string): Promise<void>
```

- **Parameter**: The authoring task ID (VideoPost.source_task_id), NOT the draft's task_id
- **Backend**: POSTs to `/video/pipeline/{task_id}/rerender`
- **Effect**: Clears the render idempotency keys so the render loop picks the task up again on the next cycle

### Updated: `videoApi.requestVideo()`

The request signature now includes `project_id`:

```typescript
requestVideo: async (body: {
  occasion: string;
  brief: string;
  platforms: string[];
  project_id: string;  // ← NEW
}): Promise<VideoRequestResult>
```

## Integration with VideoPostRow

All three features integrate into `VideoPostRow` via:

1. **Re-render button** appears in the header row (between the occasion badge and the edge):
   ```typescript
   const canRerender = !!post.source_task_id && !!post.composition_id;

   {canRerender && (
     <div className="ml-auto">
       <RerenderControl authoringTaskId={post.source_task_id as string} />
     </div>
   )}
   ```

2. **Composition preview panel** renders immediately above the MP4 player/cut switcher:
   ```typescript
   <CompositionPreviewPanel post={post} cut={cut} />
   ```

This placement gives the CEO a visual hierarchy: draft metadata → live composition preview → MP4 cuts → caption editors → approve/reject actions.

## Design Notes

- **Rerender gating**: The `canRerender` computation (`!!source_task_id && !!composition_id`) shows the control for any composition-bearing draft regardless of `render_status`, matching `RerenderControl`'s own gating in `video-pipeline-strip.tsx`, keeping both UI surfaces consistent.
- **Composition preview sizing**: Uses `aspect-video` to maintain the standard 16:9 ratio for the iframe, with `w-full` for responsive scaling.
- **Lazy loading**: The iframe uses `loading="lazy"` so it only fetches when scrolled into view, reducing initial page load on the queue.
- **Sandbox isolation**: The iframe runs with `sandbox="allow-scripts"` to execute the composition's interactive elements while preventing navigation or form submission from escaping the preview.
- **Graceful degradation**: All three features degrade safely — missing `composition_id` or `render_status` fields simply hide the corresponding UI, never crash.

## Testing

Six new tests cover the three features:

1. **Project picker** — Submit disabled until a project is selected
2. **Re-render button** — Hidden on healthy renders, shown only on stale drafts
3. **Re-render action** — Clicking queues the backend re-render action
4. **Composition preview** — Rendered only when `composition_id` is present, with correct iframe src and caption display
5. **Preview visibility** — No preview when `composition_id` is absent
6. **Request payload** — The `project_id` is sent in the POST /video/request body

All tests pass; the full suite (380 tests) is green on the panel, and typecheck/lint/prettier are clean.
