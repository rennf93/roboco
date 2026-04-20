"""
MCP Servers for RoboCo

These MCP servers bridge Claude Code agents to the RoboCo APIs,
providing tool interfaces with built-in enforcement and guidance.

Servers:
- Task MCP Server: Task lifecycle management
- Message MCP Server: Channel messaging
- Notify MCP Server: Formal notifications
- Journal MCP Server: Personal journaling
- Optimal MCP Server: Knowledge base and RAG
- A2A MCP Server: Agent-to-Agent protocol (peer-to-peer)
- Project MCP Server: Project and workspace management

Do NOT eagerly re-export `create_*_mcp_server` here. Each server is launched
as its own subprocess via `python -m roboco.mcp.<name>`, and importing the
`roboco.mcp` package first forces every sibling module to load — most
notably `optimal_server`, which pulls in piragi/ollama and adds ~6s to
startup. Claude Code v2.1.114+ times out slow MCP servers during init,
which manifests as "roboco-task/message/journal tools never register".
Keep this file empty-of-imports; callers import the specific module they
need directly.
"""
