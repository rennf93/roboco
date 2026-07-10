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
- **Python 3.13** via `/app/.venv`
- **Playwright** library (sync API) installed in the venv
- **chromium-headless-shell** browser only (no firefox or webkit)
- **System dependencies** for headless rendering (via `playwright install --with-deps chromium-headless-shell`)

### Basic headless launch

```python
/app/.venv/bin/python -c "
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto('http://localhost:3000/some-route')
    
    # Your assertions here
    
    browser.close()
"
```

Replace `http://localhost:3000/some-route` with the actual route under test.

### Frontend QA examples

**Check computed styles (after a CSS change):**

```python
/app/.venv/bin/python -c "
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto('http://localhost:3000/dashboard')
    
    # Get computed styles
    computed = page.locator('.sidebar').evaluate('el => window.getComputedStyle(el).backgroundColor')
    print('Computed background:', computed)
    assert computed != 'rgba(0, 0, 0, 0)', 'Background should not be transparent'
    
    browser.close()
"
```

**Verify accessibility tree (after a semantic HTML change):**

```python
/app/.venv/bin/python -c "
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto('http://localhost:3000/form-page')
    
    # Get accessibility tree
    snapshot = page.accessibility.snapshot()
    print('A11y tree:', snapshot)
    
    # Verify a form button is properly labeled
    form_button = [node for node in snapshot['children'] if node.get('name') == 'Submit']
    assert form_button, 'Form should have a Submit button in accessibility tree'
    
    browser.close()
"
```

### UX QA examples

**Check layout in a specific viewport (responsive design verification):**

```python
/app/.venv/bin/python -c "
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    
    # Test desktop viewport
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    page.goto('http://localhost:3000/home')
    page.screenshot(path='/tmp/review-desktop.png')
    
    browser.close()

print('Screenshot saved to /tmp/review-desktop.png')
"
```

**Verify CSS grid or flex layout after a design change:**

```python
/app/.venv/bin/python -c "
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    page.goto('http://localhost:3000/kanban')
    
    # Get bounding boxes of cards to verify layout
    card_boxes = page.locator('[data-testid=card]').all_bounding_boxes()
    print(f'Card count: {len(card_boxes)}')
    
    # Check cards are in a grid (same x-positions per row)
    x_positions = [box['x'] for box in card_boxes]
    unique_x = len(set(x_positions))
    print(f'Unique x-positions (columns): {unique_x}')
    
    browser.close()
"
```

**Take a screenshot for visual review:**

```python
/app/.venv/bin/python -c "
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    page.goto('http://localhost:3000/new-design')
    page.screenshot(path='/tmp/review.png')
    browser.close()

print('Visual review screenshot saved: /tmp/review.png')
"
```

## Important notes

### Limitations

- **Only chromium-headless-shell** is installed; `p.chromium` is the only browser type available
- `p.chromium.launch()` automatically runs in headless mode — no visual browser window will open
- The screenshot is saved to the container's filesystem (e.g., `/tmp/review.png`), not downloaded to your host
- JavaScript is enabled; async DOM updates may require `page.wait_for_timeout()` or navigation waits
- No console output capture is provided; use Playwright's logging if needed for debugging

### Documentation

- **Playwright sync API docs:** https://playwright.dev/python/docs/api/class-browser
- **Common methods:**
  - `page.goto(url)` — Navigate to a URL
  - `page.locator(selector)` — Query elements (CSS selector)
  - `page.evaluate(js_code)` — Run JavaScript in the page context
  - `page.accessibility.snapshot()` — Get the accessibility tree
  - `page.screenshot(path=...)` — Take a screenshot
  - `page.wait_for_timeout(ms)` — Wait (for animations, etc.)
  - `page.content()` — Get the full HTML source

### Journaling your verification

Always record browser-based verification the same way as any other evidence:

```
note(scope='learning', text='Verified sidebar computed background color matches design token via headless chromium after CSS refactor')
```

This documents what you checked and how for future reference.

## Troubleshooting

**"ModuleNotFoundError: No module named 'playwright'"**
- The Playwright package is installed in the venv; ensure you're using the full venv path: `/app/.venv/bin/python`

**"Timeout waiting for target..."**
- The app may not be running on `localhost:3000`. Check that the dev server is up.
- Increase timeout: `page.goto(..., timeout=30000)` (milliseconds)

**"Browser exited unexpectedly"**
- Insufficient memory or display resources in the container. Chromium headless is lightweight but needs some system memory.
- If screenshots hang, reduce viewport size or try without screenshot.

**"No screenshots appear"**
- Screenshots are saved to the container's filesystem (e.g., `/tmp/review.png`), not your host. They're local to the QA agent's environment and cannot be downloaded directly.
- If you need visual output for review, use `page.evaluate()` to extract element properties or accessibility snapshots instead.

## Why an orchestrator.py Playwright allowance is a no-op

QA and UX QA agents already run Playwright without any orchestrator-level "allowance" — there is no gate in `roboco/runtime/orchestrator.py` that could grant or withhold Playwright access, because none of the three places such a change would need to touch actually govern it. This section is the explicit written analysis for anyone who later proposes adding one.

**1. `_get_role_permissions` only scopes Write/Edit, never Bash.** `_get_role_permissions` (`roboco/runtime/orchestrator.py:1472`) builds a per-role Claude Code allow/deny list, and every role's entries in that function are `Write(...)`/`Edit(...)` patterns — the `qa` role's own entry (`roboco/runtime/orchestrator.py:1504-1511`) only denies `Write(*)`/`Edit(*)`. Playwright is invoked as `/app/.venv/bin/python -c "..."`, a `Bash` tool call, and no role's config in `_get_role_permissions` ever adds a `Bash` allow or deny entry for it. There is therefore nothing in `_get_role_permissions` that "allows" or "blocks" Playwright either way — the invocation is already permitted by the same unrestricted-Bash default every other shell command gets.

**2. The browser binary and its env var are baked into the image, not set by the orchestrator.** `chromium-headless-shell` and `PLAYWRIGHT_BROWSERS_PATH` are installed and exported at image build time in `docker/agent-qa-fe.Dockerfile` and `docker/agent-ux.Dockerfile` (`ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers`, followed by `RUN npm install -g playwright && playwright install --with-deps chromium-headless-shell`). Neither `_generate_agent_settings` nor `_prepare_agent_spawn` in `roboco/runtime/orchestrator.py` sets this environment variable or mounts a browser at spawn time — whether the browser is reachable is decided once, when the image is built, not per-spawn by orchestrator code.

**3. Playwright's sync API is a subprocess call, not an MCP tool, so no manifest entry is needed.** Every state-changing or gated capability an agent gets routes through the `mcp__roboco-flow__*`/`mcp__roboco-do__*` verbs listed in `role_config.py` and mounted as `/app/tool-manifest.json`. Playwright is called directly via its sync Python API inside a `Bash`-invoked subprocess (see the examples above) — it is never registered as an MCP tool, so there is no manifest entry to add, remove, or gate for a role to "get" Playwright access.

Put together: an orchestrator.py change that tried to add a Playwright "allowance" would have no code path left to attach to — the browser is already installed, the env var is already set, and the invocation method (Bash plus the sync API) is already unrestricted for any role that isn't Write/Edit-denied on the invoking shell. That is why QA's own browser verification works today with zero orchestrator involvement, and why a dedicated allowance change is a no-op.

## Related documentation

- **Identity prompts:** `agents/prompts/identities/fe-qa.md` and `ux-qa.md` contain the built-in browser verification guidance
- **CI verification:** `.github/workflows/agent-image-smoke.yml` runs a real headless smoke test to verify the Playwright + chromium installation on each image build
- **Diff review:** Always start with code review; browser verification is a supplement, not a replacement
