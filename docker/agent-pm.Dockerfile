# PM Agent - Lightweight coordinator
# PMs don't code, they coordinate and delegate

FROM roboco-agent-base

# No additional tools needed - PMs use MCP tools only
# They get: task management, messaging, notifications, journaling

USER root

# Playwright + chromium-headless-shell for the Product Owner's Dogfood walk
# (spec: docs/internal/specs/2026-07-24-board-programs-design.md §4) — the
# ONE board-role program that gets browser tools, task-scoped to a
# source=board_dogfood spawn only (roboco/runtime/orchestrator.py
# _is_dogfood_spawn). "product-owner" shares this image with main-pm/cell-pm/
# head-marketing/auditor (AGENT_IMAGES), so every board/PM container carries
# the binary even though only a dogfood-task PO spawn ever gets it mounted —
# mirrors agent-qa-fe/agent-ux's identical bake-once, mount-conditionally
# shape. Browsers land in /app/.playwright (not /app/.venv) so the closing
# chown only touches this new ~small dir, never the pre-owned .venv tree (a
# chown -R over .venv would COW-duplicate the whole thing into this layer —
# see the 0.19.0 lean-images CHANGELOG note on agent-grok's chown -R).
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.playwright
RUN uv pip install --python /app/.venv/bin/python playwright \
    && /app/.venv/bin/playwright install --with-deps chromium-headless-shell \
    && chown -R agent:agent /app/.playwright

# Playwright MCP server — structured browser tools (navigate/click/snapshot/
# screenshot), registered by the orchestrator only for a product_owner spawn
# on a source=board_dogfood task (see roboco/runtime/orchestrator.py
# _generate_mcp_config / _is_dogfood_spawn). Pinned version; the wrapper
# entrypoint below points it at this image's baked chromium-headless-shell
# instead of letting it download its own bundled browser.
RUN npm install -g @playwright/mcp@0.0.78 \
    && npm cache clean --force
COPY docker/scripts/playwright-mcp-entrypoint.sh /app/scripts/playwright-mcp-entrypoint.sh
RUN chmod 0755 /app/scripts/playwright-mcp-entrypoint.sh

USER agent

LABEL role="pm"
LABEL description="Project Manager agent - coordinates work, delegates to developers"
