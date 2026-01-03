# RAG Knowledge Base Documentation

Optimized documentation for the RoboCo AI agent knowledge base. Each file is sized for effective RAG chunking.

## Structure

```
docs/rag/
├── roles/           # Agent role responsibilities
├── workflows/       # Step-by-step task flows
├── standards/       # Coding, security, testing rules
├── architecture/    # System components
├── tools/           # MCP tools reference
└── troubleshooting/ # Common issues and fixes
```

## Organization Principles

1. **One topic per file** - Each file covers a single concept
2. **Chunk-friendly** - Content fits in 512-1536 token chunks
3. **Self-contained** - Each file provides complete context
4. **Actionable** - Focus on what agents need to DO

## For Agents

When searching the knowledge base:
- Use `roboco_kb_search()` for semantic search
- Use `roboco_rag_query()` for AI-synthesized answers
- Use `roboco_ask_mentor()` for conversational help

See `/docs/workflows/KNOWLEDGE_BASE.md` for full KB tool reference.
