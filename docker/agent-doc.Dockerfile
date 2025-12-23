# Documenter Agent
# Lightweight - documentation doesn't need heavy tools

FROM roboco-agent-base

# No additional tools needed
# Documenters write markdown, update READMEs, changelogs
# They use the mounted /app/docs directory

LABEL role="documenter"
LABEL description="Documenter agent - technical writing, API docs, changelogs"
