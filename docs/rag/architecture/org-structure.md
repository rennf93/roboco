# Organizational Structure

## Hierarchy

```
CEO (Renzo - Human)
    |
    +-- Board (3 agents)
         +-- Product Owner
         +-- Head of Marketing
         +-- Auditor (silent observer)
              |
              +-- Main PM
                   |
                   +-- Backend Cell
                   +-- Frontend Cell
                   +-- UX/UI Cell
```

## Agent Count

| Role | Count |
|------|-------|
| CEO | 1 (human) |
| Product Owner | 1 |
| Head of Marketing | 1 |
| Auditor | 1 |
| Main PM | 1 |
| Cell PMs | 3 |
| Developers | 6 (2 per cell) |
| QAs | 3 (1 per cell) |
| Documenters | 3 (1 per cell) |
| **Total** | **20** (19 AI + 1 human) |

## Cells

| Cell | PM | Developers | QA | Documenter |
|------|-----|------------|-----|------------|
| Backend | be-pm | be-dev-1, be-dev-2 | be-qa | be-doc |
| Frontend | fe-pm | fe-dev-1, fe-dev-2 | fe-qa | fe-doc |
| UX/UI | ux-pm | ux-dev-1, ux-dev-2 | ux-qa | ux-doc |

## Teams

| Team | Members |
|------|---------|
| executive | ceo |
| board | product-owner, head-marketing, auditor |
| management | main-pm, be-pm, fe-pm, ux-pm |
| developers | all devs |
| qa | all QAs |
| documentation | all documenters |

## Escalation Chain

```
Developer/QA/Documenter → Cell PM → Main PM → Product Owner → CEO
```

## Communication

Each role can communicate with:

| Role | Can Communicate With |
|------|---------------------|
| CEO | Everyone |
| Board | CEO, other board, Main PM |
| Auditor | Everyone (silent read all) |
| Main PM | CEO, Board, Cell PMs |
| Cell PM | Main PM, cell members |
| Cell Members | Cell PM, other cell members |
