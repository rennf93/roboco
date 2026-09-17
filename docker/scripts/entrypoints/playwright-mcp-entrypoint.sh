#!/bin/bash
# Playwright MCP entrypoint — resolves the chromium-headless-shell binary
# already baked into this image at build time (docker/agent-qa-fe.Dockerfile
# / docker/agent-ux.Dockerfile, `playwright install --with-deps
# chromium-headless-shell`) and execs the Playwright MCP server against it.
#
# `@playwright/mcp` bundles its own `playwright-core` and would otherwise
# download a second copy of Chromium on first use; pointing it at the
# already-installed browser via `--executable-path` keeps this image at one
# browser, not two (see the 0.19.0 lean-images CHANGELOG note this Dockerfile
# is scoped against).
set -euo pipefail

CHROMIUM_PATH="$(/app/.venv/bin/python -c '
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    print(p.chromium.executable_path)
')"

exec playwright-mcp --executable-path "$CHROMIUM_PATH" --headless --isolated
