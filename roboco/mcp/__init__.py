"""
MCP Servers for RoboCo

These MCP servers bridge Claude Code agents to the RoboCo APIs,
providing tool interfaces with built-in enforcement and guidance.

Servers:
- Flow MCP Server (``flow_server``)         intent verbs (lifecycle)
- Do MCP Server (``do_server``)             content tools (commit, push,
                                            PR, journal, notify, message)
- Git Read-Only MCP Server (``git_readonly``) status, log, diff, branches
- Optimal MCP Server (``optimal_server``)   knowledge base and RAG
- Docs MCP Server (``docs_server``)         documentation file management

Do NOT eagerly re-export server factories here. Each server is launched
as its own subprocess via ``python -m roboco.mcp.<name>``, and importing
the ``roboco.mcp`` package first forces every sibling module to load —
most notably ``optimal_server``, which pulls in the pgvector/ollama stack and adds
~6s to startup. Claude Code times out slow MCP servers during
init, which manifests as "roboco-flow/do tools never register". Keep
this file empty-of-imports; callers import the specific module they
need directly.
"""
