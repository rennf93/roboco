# Knowledge Base

The **Knowledge Base** page (`/knowledge-base`) is your window into RoboCo's in-house RAG — the pgvector-backed retrieval engine the agents use to find prior context. From here you search the indexes, ask synthesized questions and get answers with citations, talk to the mentor, browse by category, and (in the Admin tab) reindex or clear the indexes. The page is organized into five tabs, with the active tab held in the URL.

## Search

Free-text search across the indexes, with an optional filter to scope the query to specific index types. This is the raw retrieval view — it returns the matching chunks so you can see exactly what the agents would find.

## Ask

The RAG question-and-answer surface. You ask a question in natural language and get a **synthesized answer with citations** back to the source documents, rather than a raw chunk list. This is the layer that turns the indexes into an answer.

## Mentor

A mentor chat — the same `roboco_ask_mentor` capability the agents reach through their gateway, exposed to you conversationally for asking about the codebase and accumulated knowledge.

## Browse

Browse the indexed content **by category** rather than by query, when you want to see what's in an index instead of searching for something specific.

## Admin

The control surface for the indexes themselves. The KB is split into several index types — Documentation, Conversations, Agent Journals, Error Solutions, Standards, Decisions, Code Reviews, and Learnings — and the Admin tab shows **per-index stats** and lets you maintain them:

| Action | Effect |
|--------|--------|
| **Reindex all** | rebuild every index from source (force) |
| **Refresh** *(per index)* | re-pull and re-embed that one index |
| **Delete** *(per index)* | clear that index's contents |

!!! warning "Delete and reindex are operational actions"
    Clearing an index removes its embeddings, and a full reindex re-embeds everything from scratch — both take real work and real Ollama time. Delete is guarded behind a confirmation dialog. Treat the Admin tab as a maintenance surface, not a daily one.

## It depends on Ollama

The whole page — search, ask, mentor, and especially reindexing — runs on the embedding model served by **Ollama** (`qwen3-embedding:0.6b`). If Ollama isn't healthy, queries return nothing useful and reindexing can't embed.

!!! info "If results come back empty"
    A common first-run symptom is an empty or failing KB while Ollama is still pulling models, or when the embedding endpoint is unreachable. Check that the `ollama` service is up and the model is pulled — see [common issues](../troubleshooting/common-issues.md). The RAG engine and its configuration are covered under [deployment](../deploy/deployment.md).

## Next

→ [Communications & journals](./communications-and-journals.md) — the raw conversations and journals that feed several of these indexes — or [the Auditor](./auditor.md) for quality oversight.
