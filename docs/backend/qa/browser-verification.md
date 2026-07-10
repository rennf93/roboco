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

## Related documentation

- **Identity prompts:** `agents/prompts/identities/fe-qa.md` and `ux-qa.md` contain the built-in browser verification guidance
- **CI verification:** `.github/workflows/agent-image-smoke.yml` runs a real headless smoke test to verify the Playwright + chromium installation on each image build
- **Diff review:** Always start with code review; browser verification is a supplement, not a replacement
