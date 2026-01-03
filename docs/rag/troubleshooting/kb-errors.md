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

## Cannot Clear Index

**Problem**: "Not authorized to clear index"

**Cause**: Only PM/CEO can clear indexes

**Solution**: Ask PM or CEO to clear if needed

## Proactive Context Empty

**Problem**: `roboco_get_proactive_context()` returns empty

**Cause**: No relevant context found for task

**Solution**: Manual search with `roboco_kb_search()` using task keywords
