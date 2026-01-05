# Tool Permissions by Role

## Overview

Agents have role-specific tool permissions enforced via Claude Code settings.
Native tools are blocked; use `roboco_*` MCP tools instead.

## Developer

**Allowed:**
- `roboco_task_*` - task lifecycle
- `roboco_git_*` - all git operations
- `roboco_test_*` - run tests, lint, format
- `roboco_journal_*` - journaling
- `roboco_kb_*`, `roboco_rag_*` - knowledge base
- `Read(*)` - read any file
- `Write/Edit` - workspace only

**Blocked:**
- `Bash(git:*)` - use roboco_git_* instead
- `Write/Edit` outside workspace

**Workspace:** `/data/workspaces/{project}/{team}/{agent-id}/`

## QA

**Allowed:**
- `roboco_git_status`, `roboco_git_log`, `roboco_git_diff` - read-only
- `roboco_test_*` - run tests
- `roboco_task_qa_pass`, `roboco_task_qa_fail`
- `Read(*)` - read any file

**Blocked:**
- `roboco_git_commit`, `roboco_git_push` - QA doesn't write code
- All `Write/Edit` - review only

## Documenter

**Allowed:**
- `roboco_docs_*` - documentation tools
- `roboco_git_*` - all git operations
- `Write/Edit` in `/app/docs/**` only

**Blocked:**
- `Write/Edit` outside docs directory

## PM (Cell PM, Main PM)

**Allowed:**
- `roboco_git_*` - all git operations
- `roboco_docs_*` - documentation
- `roboco_task_*` - full task management
- `roboco_notify_send` - send notifications

**Blocked:**
- `Bash(git:*)` - use roboco_git_*

## Auditor

**Allowed:**
- `roboco_git_status`, `roboco_git_log`, `roboco_git_diff` - read-only
- `Read(*)` - read any file

**Blocked:**
- All write operations - observer role
