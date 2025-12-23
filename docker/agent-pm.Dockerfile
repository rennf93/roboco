# PM Agent - Lightweight coordinator
# PMs don't code, they coordinate and delegate

FROM roboco-agent-base

# No additional tools needed - PMs use MCP tools only
# They get: task management, messaging, notifications, journaling

LABEL role="pm"
LABEL description="Project Manager agent - coordinates work, delegates to developers"
