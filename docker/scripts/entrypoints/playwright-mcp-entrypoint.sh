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
#
# The path is the headless shell's own executable, found under the browsers
# directory. It used to come from Playwright's `chromium.executable_path`,
# which names FULL Chromium (chromium-<rev>/chrome-linux64/chrome) - a browser
# these images never install - so the MCP server was handed a binary that
# does not exist and the QA agents' browser tool could not launch.
#
# `--print-executable-path` prints the resolved path and exits, so the image
# smoke workflow checks exactly what this wrapper would run.
set -euo pipefail
shopt -s nullglob

browsers_dir="${PLAYWRIGHT_BROWSERS_PATH:-/app/.playwright}"
candidates=("$browsers_dir"/chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell)

if [ "${#candidates[@]}" -ne 1 ] || [ ! -x "${candidates[0]}" ]; then
  echo "playwright-mcp-entrypoint: expected exactly one executable chromium-headless-shell under $browsers_dir, found ${#candidates[@]}: ${candidates[*]:-none}" >&2
  exit 1
fi

if [ "${1:-}" = "--print-executable-path" ]; then
  echo "${candidates[0]}"
  exit 0
fi

exec playwright-mcp --executable-path "${candidates[0]}" --headless --isolated
