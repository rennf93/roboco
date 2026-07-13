# UX/UI Agent
# Design tools - future: Figma MCP, image generation

FROM roboco-agent-base

# Future additions:
# - Figma MCP server integration
# - Image generation tools
# - Design token management

USER root

# Playwright + chromium-headless-shell for UX-QA's browser-based design/
# rendering verification. No dedicated ux-qa image exists — ux-qa runs this
# same image as ux-dev (see orchestrator.py IMAGE_MAP), so ux-dev picks this
# up too. Browsers land in /app/.playwright (not /app/.venv) so the closing
# chown only touches this new dir, never the pre-owned .venv tree (a
# chown -R over .venv would COW-duplicate the whole thing into this layer —
# see the 0.19.0 lean-images CHANGELOG note on agent-grok's chown -R).
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.playwright
RUN uv pip install --python /app/.venv/bin/python playwright \
    && /app/.venv/bin/playwright install --with-deps chromium-headless-shell \
    && chown -R agent:agent /app/.playwright

# Playwright MCP server — structured browser tools (navigate/click/snapshot/
# screenshot) for QA's browser verification, registered by the orchestrator
# for the fe-qa/ux-qa roles only (see roboco/runtime/orchestrator.py
# _generate_mcp_config). Pinned version; the wrapper entrypoint below points
# it at this image's baked chromium-headless-shell instead of letting it
# download its own bundled browser. ux-dev shares this image but never gets
# the MCP registration (role-gated, not image-gated).
RUN npm install -g @playwright/mcp@0.0.78 \
    && npm cache clean --force
COPY docker/scripts/playwright-mcp-entrypoint.sh /app/scripts/playwright-mcp-entrypoint.sh
RUN chmod 0755 /app/scripts/playwright-mcp-entrypoint.sh

USER agent

LABEL role="ux-designer"
LABEL description="UX/UI agent - design, prototyping, design system"
