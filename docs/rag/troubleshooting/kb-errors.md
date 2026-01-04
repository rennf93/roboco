# Knowledge Base Troubleshooting

## Empty Search Results

**Problem**: `roboco_kb_search()` returns nothing

**Causes**:
1. Content not indexed yet
2. Query too specific
3. Wrong index type filter

**Solutions**:
- Check what's indexed: `roboco_kb_stats()`
- Broaden query terms
- Remove index_types filter
- Trigger reindex: `roboco_reindex_all()`

## Empty RAG Response

**Problem**: `roboco_rag_query()` returns empty answer

**Causes**:
1. No relevant context found
2. LLM returned thinking tags only
3. Query too vague

**Solutions**:
- Check KB has relevant content
- Rephrase query to be more specific
- Use `roboco_kb_search()` first to verify content exists

## Mentor Not Responding

**Problem**: `roboco_ask_mentor()` fails or empty

**Causes**:
1. LLM timeout
2. No relevant KB content
3. Service temporarily unavailable

**Solutions**:
- Retry the query
- Check KB stats
- Use `roboco_kb_search()` as fallback

## Index Failed

**Problem**: `roboco_kb_index_code()` or `roboco_kb_index_docs()` fails

**Causes**:
1. Invalid file patterns
2. Files not accessible
3. Embedding service down

**Solutions**:
- Verify file patterns match files
- Check file permissions
- Verify Ollama is running

## Documentation Write Failed

**Problem**: `roboco_docs_write()` fails

**Causes**:
1. Invalid doc_type (must be: api, qa, guide, readme, changelog, architecture, design)
2. Missing required fields (task_id, filename, title, content)
3. Agent not authorized (only documenter and cell_pm roles)
4. Task not found

**Solutions**:
- Verify doc_type is valid
- Ensure all required fields provided
- Check your role has write permission
- Verify task_id exists

## Duplicate Documentation Created

**Problem**: Created duplicate docs instead of updating existing

**Causes**:
1. Content too different from existing doc (RAG similarity < 0.75)
2. Doc in different team folder
3. RAG search failed (but write still succeeded)

**Solutions**:
- Ensure content covers same topic as existing doc
- Check existing docs first: `roboco_docs_list(task_id)`
- Search KB: `roboco_kb_search("topic keywords")`
- Delete duplicate if needed: `roboco_docs_delete(path)`

**Note**: `roboco_docs_write()` uses RAG to auto-deduplicate by **content similarity** (not just title). If content is semantically similar (>75% similarity), it updates instead of creating new.

## Cannot Clear Index

**Problem**: "Not authorized to clear index"

**Cause**: Only PM/CEO can clear indexes

**Solution**: Ask PM or CEO to clear if needed

## Proactive Context Empty

**Problem**: `roboco_get_proactive_context()` returns empty

**Cause**: No relevant context found for task

**Solution**: Manual search with `roboco_kb_search()` using task keywords
