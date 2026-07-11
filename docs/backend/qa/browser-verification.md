# Browser Verification with Playwright

**For:** Frontend QA (`fe-qa`) and UX QA (`ux-qa`) agents

**Purpose:** Verify rendered output, computed styles, a11y trees, and visual design when code review and diff analysis cannot settle an acceptance criterion

## When to use browser verification

Browser verification is a **verification aid** — use it when reading the diff cannot confirm an acceptance criterion and you need rendered output or runtime behavior.

**Read the diff first.** This is your default verification method. Browser verification supplements it in cases like:

- **Rendered output:** Layout, typography, color application, visual hierarchy (after CSS changes)
- **Computed styles:** Verifying actual applied styles vs. declared styles (cascading, overrides, media queries)
- **Accessibility:** a11y tree structure, ARIA attributes, semantic correctness, contrast (via accessibility snapshot)
- **Visual design:** Spatial relationships, viewport-specific behavior, responsive breakpoints (UX QA focus)
- **Dynamic behavior:** DOM updates after JavaScript runs, interactive states (hover, focus, disabled)

**Examples of what you CAN'T use this for:**
- Rendering interactions that require user input (animations triggered by user gestures)
- Cross-browser compatibility (only chromium-headless-shell is available)
- Performance metrics (synthetic browser is not production-representative)
- Network behavior (no network mocking setup provided)
- Audio/video playback

## Setup and examples

### What's installed

The QA image (`agent-qa-fe` for Frontend, `agent-ux` for UX) ships:
- **Python 3.13** via `/app/.venv`, with the **Playwright** library (sync API) installed in the venv
- **chromium-headless-shell** browser only (no firefox or webkit), plus system deps for headless rendering (via `playwright install --with-deps chromium-headless-shell`)
- A **`playwright` MCP server** (`@playwright/mcp`, `mcp__playwright__*` tools) registered for the `fe-qa`/`ux-qa` roles only, wired to run against that same baked chromium-headless-shell via a wrapper entrypoint (`docker/scripts/playwright-mcp-entrypoint.sh`) — see `roboco/runtime/orchestrator.py`'s `_generate_mcp_config`

Drive the browser through the structured `mcp__playwright__*` tools — hand-scripting the Python sync API against a live agent is fragile (multi-line `Bash -c` strings, no structured error surface); the MCP tools give you `browser_navigate`, `browser_snapshot`, `browser_evaluate`, `browser_take_screenshot`, and `browser_close` directly as first-class tool calls.

### Basic flow

    mcp__playwright__browser_navigate(url="http://localhost:3000/some-route")
    # ... inspect via browser_snapshot / browser_evaluate / browser_take_screenshot ...
    mcp__playwright__browser_close()

Replace `http://localhost:3000/some-route` with the actual route under test.

### Frontend QA examples

**Check computed styles (after a CSS change):**

    mcp__playwright__browser_navigate(url="http://localhost:3000/dashboard")
    mcp__playwright__browser_evaluate(
        function="() => window.getComputedStyle(document.querySelector('.sidebar')).backgroundColor"
    )
    # Assert the returned value isn't transparent ("rgba(0, 0, 0, 0)")
    mcp__playwright__browser_close()

**Verify accessibility tree (after a semantic HTML change):**

    mcp__playwright__browser_navigate(url="http://localhost:3000/form-page")
    mcp__playwright__browser_snapshot()
    # Confirm the tree includes a "Submit" button node
    mcp__playwright__browser_close()

### UX QA examples

**Check layout in a specific viewport (responsive design verification):**

    mcp__playwright__browser_navigate(url="http://localhost:3000/home")
    mcp__playwright__browser_resize(width=1280, height=800)
    mcp__playwright__browser_take_screenshot(filename="review-desktop.png")
    mcp__playwright__browser_close()

**Verify CSS grid or flex layout after a design change:**

    mcp__playwright__browser_navigate(url="http://localhost:3000/kanban")
    mcp__playwright__browser_resize(width=1280, height=800)
    mcp__playwright__browser_snapshot(boxes=True)  # bounding boxes per element, to compare card x-positions
    mcp__playwright__browser_close()

**Take a screenshot for visual review:**

    mcp__playwright__browser_navigate(url="http://localhost:3000/new-design")
    mcp__playwright__browser_resize(width=1280, height=800)
    mcp__playwright__browser_take_screenshot(filename="review.png")
    mcp__playwright__browser_close()

## Important notes

### Limitations

- **Only chromium-headless-shell** is available — the MCP server's `--executable-path` (set by the wrapper entrypoint) points at this image's baked browser only, no firefox/webkit
- Runs headless (`--headless`) with an in-memory profile (`--isolated`) — no visual browser window, no persisted state between sessions
- Screenshots/snapshots saved via `filename` land in the MCP server's output directory inside the container, not on your host
- JavaScript is enabled; async DOM updates may need `browser_wait_for` before snapshotting

### Documentation

- **Playwright MCP tool reference:** run `npx @playwright/mcp@latest --help` for CLI flags, or see the [`@playwright/mcp` README](https://github.com/microsoft/playwright-mcp) for the full tool list
- **Common tools:**
  - `browser_navigate(url)` — Navigate to a URL
  - `browser_snapshot()` — Accessibility-tree snapshot of the current page (preferred over a screenshot for verifying structure)
  - `browser_evaluate(function)` — Run JavaScript in the page context
  - `browser_take_screenshot(filename)` — Take a screenshot
  - `browser_resize(width, height)` — Set the viewport size
  - `browser_wait_for(text | textGone | time)` — Wait for content or a fixed delay
  - `browser_close()` — Close the browser

### Journaling your verification

Always record browser-based verification the same way as any other evidence:

```
note(scope='learning', text='Verified sidebar computed background color matches design token via headless chromium after CSS refactor')
```

This documents what you checked and how for future reference.

## Troubleshooting

**MCP tool call errors immediately**
- Confirm you're on `fe-qa` or `ux-qa` — the `playwright` MCP server is role-gated and won't appear in any other role's tool set (see `roboco/runtime/orchestrator.py` `_generate_mcp_config`).

**"Timeout waiting for target..." / navigation hangs**
- The app may not be running on `localhost:3000`. Check that the dev server is up.
- Use `browser_wait_for(text=...)` after `browser_navigate` if the target renders asynchronously.

**"Browser exited unexpectedly"**
- Insufficient memory or display resources in the container. Chromium headless is lightweight but needs some system memory.

**No screenshot/snapshot output appears**
- Files saved via `filename` are local to the QA agent's environment and cannot be downloaded directly — use `browser_evaluate` or `browser_snapshot`'s inline (non-file) response instead when you need the content back in the conversation.

## Why the Playwright MCP registration is role-gated, not orchestrator-Bash-gated

Earlier versions of this doc argued that any orchestrator-level Playwright "allowance" would be a no-op, because the original design ran Playwright by hand-scripting its Python sync API through an unrestricted `Bash` call. That's no longer the whole picture: the `playwright` MCP server itself **is** an orchestrator-level gate — `_generate_mcp_config` registers it only when `get_agent_role(agent_id) == "qa"` and `get_agent_team(agent_id)` is `frontend` or `ux_ui`, so `be-qa` (backend QA, same role, different team) and `ux-dev` (same image as `ux-qa`, different role) never see `mcp__playwright__*` in their tool set even though the npm package and the baked browser exist in their image or a sibling image. The underlying Bash+Python path from the original no-op analysis still exists and is still unrestricted for any role that isn't Write/Edit-denied — this doc just no longer recommends it, since the structured MCP tools are strictly more reliable for an agent to drive.

## Related documentation

- **Identity prompts:** `agents/prompts/identities/fe-qa.md` and `ux-qa.md` contain the built-in browser verification guidance
- **CI verification:** `.github/workflows/agent-image-smoke.yml` runs a real headless smoke test to verify the Playwright + chromium installation, the `playwright-mcp` binary, and a real panel-page screenshot on each image build
- **Diff review:** Always start with code review; browser verification is a supplement, not a replacement
