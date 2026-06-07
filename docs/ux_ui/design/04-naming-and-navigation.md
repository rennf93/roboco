# Prompter — Naming & Navigation Specification

## Principle

The existing Panel uses plain, descriptive nouns for navigation:
- "Overview", "Tasks", "Kanban"
- "Projects", "Products", "Git"
- "Agents", "Knowledge Base", "Auditor"

A branded product name like "Prompter" risks feeling like a third-party plugin. The name should fit the existing vocabulary and signal value immediately.

---

## Naming Options

### Option A: Task Assistant *(Recommended)*

- **Label**: "Task Assistant"
- **Rationale**:
  - Plain noun + descriptor pattern (matches "Knowledge Base", "AI Providers")
  - Immediately communicates value: it helps you with tasks.
  - Does not over-promise autonomy — "assistant" implies human control.
  - Works in sentence case naturally: "Open the Task Assistant."
- **Subtitle copy**: "Draft tasks with your AI teammate."
- **Concerns**: Slightly longer than other nav items; may truncate in collapsed sidebar.
- **Mitigation**: Collapsed sidebar uses icon + tooltip; length is fine in expanded view.

### Option B: Draft

- **Label**: "Draft"

- **Rationale**:
  - Single word, action-oriented, fits the existing terse style.
  - Signals the core output: a draft task.
  - Human-centric verb — you draft, the AI helps.
- **Subtitle copy**: "Draft tasks with your AI teammate."
- **Concerns**:
  - Ambiguous: could be confused with "draft tasks" as a filter state in the Tasks page.
  - Less discoverable for users who don’t already know the feature exists.

### Option C: Composer

- **Label**: "Composer"

- **Rationale**:
  - Evokes creation and authoring.
  - Familiar to developers (IDE composers, email composers).
- **Subtitle copy**: "Compose tasks with your AI teammate."
- **Concerns**:
  - Slightly more abstract than "Task Assistant".
  - May imply musical or creative composition rather than structured task specs.

### Internal Name

"Prompter" remains the **internal engineering and marketing codename**. It is acceptable in internal documentation, Slack, and code comments. The UI label is the user-facing name chosen above.

---

## Sidebar Placement

### Recommended Placement

Insert under the **Work Management** section, between "Kanban" and "Projects":

```typescript
const navItems = [
  // Dashboard
  { title: "Overview", href: "/overview", icon: LayoutDashboard },

  // Work Management
  { title: "Tasks", href: "/tasks", icon: ListTodo },
  { title: "Kanban", href: "/kanban", icon: Kanban },
  { title: "Task Assistant", href: "/prompter", icon: Sparkles }, // NEW

  // Development
  { title: "Projects", href: "/projects", icon: FolderGit2 },
  { title: "Products", href: "/products", icon: Boxes },
  { title: "Git", href: "/git", icon: GitBranch },

  // ... rest unchanged
];
```

### Icon

Use `Sparkles` from `lucide-react` (not currently imported in `sidebar.tsx`).

- Rationale: universally understood as "AI / magic / assistance" without being overly literal.
- Alternative: `MessageSquarePlus` — more literal (chat + create), but `Sparkles` is more distinctive among existing icons.

### Active State

Same as existing nav items:
- Active: `bg-primary text-primary-foreground`
- Inactive: `text-muted-foreground hover:bg-muted hover:text-foreground`

---

## Page Title & Meta

| Surface | Copy (Option A) |
|---------|-----------------|
| Sidebar nav item | "Task Assistant" |
| Browser tab title | "Task Assistant — RoboCo Panel" |
| Page H1 | "Task Assistant" |
| Page subtitle | "Describe what you need. The assistant will ask questions and draft a task for your team." |
| Empty-state heading | "What do you want to build?" |
| Empty-state subtext | "Describe the task in plain language. The assistant will clarify and draft a spec you can review before sending it to the team." |

---

## URL

`/prompter` — keep the engineering slug regardless of display name. This avoids routing churn if the display name changes later.

- Redirects: none needed for MVP.
- Deep-linking: `/prompter` always loads the empty/chat state; there is no persisted session ID in the URL for Phase 1.

---

## Discoverability

### Primary
- Sidebar entry at all times (not hidden behind permissions or feature flags for Phase 1).

### Secondary
- Quick Actions bar on Overview dashboard: add a "New Task (AI-assisted)" button that links to `/prompter`.
  - Uses existing `QuickActionsBar` pattern in `panel/src/components/dashboard/quick-actions-bar.tsx`.
  - Icon: `Sparkles` next to the existing "New Task" button.

---

## Decision Matrix

| Criterion | Task Assistant | Draft | Composer |
|-----------|---------------|-------|----------|
| Fits existing panel vocabulary | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| Communicates value immediately | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ |
| Does not over-promise autonomy | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| Short enough for sidebar | ⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| Distinct from other pages | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ |
| Works in marketing copy | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ |
| **Total** | **16** | **13** | **12** |

**UX/UI Cell recommendation**: **Task Assistant** (Option A).

---

## Cross-Cell Handoff

- **Frontend**: implement route `/prompter`, sidebar entry with `Sparkles`, page layout.
- **Backend**: no API changes needed for naming; slug remains `prompter` in code.
- **Marketing**: "Draft tasks with your AI teammate" is the recommended tagline; aligns with "Task Assistant" label.
