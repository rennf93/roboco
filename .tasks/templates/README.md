# Task Templates

Templates for creating task records in the RoboCo task management system.

## Task Type Templates

These templates are used to create the main README.md for a task:

| Template | Use When | Key Features |
|----------|----------|--------------|
| [feature.md](feature.md) | Building new functionality | User story, acceptance criteria, technical details |
| [bugfix.md](bugfix.md) | Fixing bugs | Reproduction steps, root cause, fix verification |
| [research.md](research.md) | Investigation/research tasks | Research questions, findings, recommendations |
| [documentation.md](documentation.md) | Writing/updating docs | Source materials, publication locations |
| [design.md](design.md) | UX/UI design work | Figma links, states checklist, handoff |

## Supporting Templates

These templates are used for files within a task directory:

| Template | Purpose | Created By |
|----------|---------|------------|
| [plan.md](plan.md) | Implementation plan | Developer during planning |
| [journal.md](journal.md) | Agent journey notes | Developer during execution |
| [decisions.md](decisions.md) | Decision log | Anyone making decisions |
| [blockers.md](blockers.md) | Blocker tracking | Anyone when blocked |
| [handoff.md](handoff.md) | Documenter handoff | Developer when complete |
| [qa-review.md](qa-review.md) | QA findings | QA during review |

## Initiative Templates

In `../initiatives/_template/`:

| Template | Purpose |
|----------|---------|
| README.md | Initiative overview |
| requirements.md | Detailed requirements |
| tasks.md | Task breakdown by cell |
| timeline.md | Milestones and schedule |
| decisions.md | Cross-cell decisions |
| status.md | Status updates |

## Creating a New Task

### 1. Determine Task Type
- New feature → `feature.md`
- Bug fix → `bugfix.md`
- Research/investigation → `research.md`
- Documentation → `documentation.md`
- Design work → `design.md`

### 2. Create Task Directory
```bash
mkdir -p .tasks/active/TASK-XXX-{slug}
```

### 3. Copy Template
```bash
cp .tasks/templates/{type}.md .tasks/active/TASK-XXX-{slug}/README.md
```

### 4. Fill In Template
- Replace all `{placeholders}`
- Update status section
- Define acceptance criteria
- Add to index.md

### 5. Create Supporting Files As Needed
```bash
# Copy templates as needed during work
cp .tasks/templates/plan.md .tasks/active/TASK-XXX-{slug}/
cp .tasks/templates/journal.md .tasks/active/TASK-XXX-{slug}/
```

## Template Conventions

### Placeholders
- `{ID}` - Task number (e.g., 042)
- `{Title}` - Human-readable title
- `{agent-id}` - Agent identifier (e.g., be-dev-1)
- `YYYY-MM-DD` - ISO date format
- `{slug}` - Kebab-case description

### Status Values
- `pending` - Not started
- `claimed` - Assigned, not started
- `in_progress` - Active work
- `blocked` - Waiting on something
- `paused` - Intentionally stopped
- `verifying` - Self-review
- `awaiting_qa` - Ready for QA
- `needs_revision` - QA found issues
- `awaiting_documentation` - Ready for docs
- `completed` - Done

### Priority Values
- `P0` - Critical, drop everything
- `P1` - High, next up
- `P2` - Medium, normal queue
- `P3` - Low, when available

### Cell Values
- `backend` - Backend cell
- `frontend` - Frontend cell
- `ux_ui` - UX/UI cell
- `board` - Board level

## Tips

1. **Always update README.md** when status changes
2. **Journal frequently** - context is valuable
3. **Link commits** as you make them
4. **Create handoff.md** before marking awaiting_documentation
5. **Be specific** in acceptance criteria - vague = wasted time
