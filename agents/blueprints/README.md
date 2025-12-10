# Agent Blueprints

System prompts and configurations for all 16 RoboCo AI agents.

## All Agents Complete! ✓

### Backend Cell (4 agents)

| Agent | File | Description |
|-------|------|-------------|
| BE-Dev | [be-dev.md](be-dev.md) | Backend developer - writes Python/FastAPI code |
| BE-PM | [be-pm.md](be-pm.md) | Backend PM - assigns tasks, coordinates cell |
| BE-QA | [be-qa.md](be-qa.md) | Backend QA - reviews code, security, tests |
| BE-Documenter | [be-documenter.md](be-documenter.md) | Backend documenter - API docs, architecture |

### Frontend Cell (4 agents)

| Agent | File | Description |
|-------|------|-------------|
| FE-Dev | [fe-dev.md](fe-dev.md) | Frontend developer - React/TypeScript |
| FE-PM | [fe-pm.md](fe-pm.md) | Frontend PM - coordinates with BE-PM and UX-PM |
| FE-QA | [fe-qa.md](fe-qa.md) | Frontend QA - visual, a11y, cross-browser |
| FE-Documenter | [fe-documenter.md](fe-documenter.md) | Frontend documenter - component docs, Storybook |

### UX/UI Cell (4 agents)

| Agent | File | Description |
|-------|------|-------------|
| UX-Dev | [ux-dev.md](ux-dev.md) | UX/UI developer - Figma, design system |
| UX-PM | [ux-pm.md](ux-pm.md) | UX/UI PM - design handoffs to Frontend |
| UX-QA | [ux-qa.md](ux-qa.md) | UX/UI QA - design consistency, a11y, completeness |
| UX-Documenter | [ux-documenter.md](ux-documenter.md) | UX/UI documenter - design system docs |

### Management (1 agent)

| Agent | File | Description |
|-------|------|-------------|
| Main PM | [main-pm.md](main-pm.md) | Coordinates all cells, reports to Board |

### Board Level (3 agents)

| Agent | File | Description |
|-------|------|-------------|
| Product Owner | [product-owner.md](product-owner.md) | Defines requirements, prioritizes, accepts work |
| Head of Marketing | [head-marketing.md](head-marketing.md) | Marketing strategy, launches, campaigns |
| Auditor | [auditor.md](auditor.md) | Silent observer, reports to CEO |

## Organization Chart

```
                              ┌─────────────┐
                              │     CEO     │
                              │   (Human)   │
                              └──────┬──────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
              ┌─────▼─────┐    ┌─────▼─────┐    ┌─────▼─────┐
              │  Product  │    │   Head    │    │  Auditor  │
              │   Owner   │    │ Marketing │    │   (Spy)   │
              └─────┬─────┘    └─────┬─────┘    └───────────┘
                    │                │                ▲
                    └───────┬────────┘                │
                            │                    [observes all]
                     ┌──────▼──────┐
                     │   Main PM   │
                     └──────┬──────┘
                            │
       ┌────────────────────┼────────────────────┐
       │                    │                    │
 ┌─────▼─────┐        ┌─────▼─────┐        ┌─────▼─────┐
 │  BE-PM    │        │  FE-PM    │        │  UX-PM    │
 ├───────────┤        ├───────────┤        ├───────────┤
 │ BE-Dev ×2 │        │ FE-Dev ×2 │        │ UX-Dev    │
 │ BE-QA     │        │ FE-QA     │        │ UX-QA     │
 │ BE-Doc    │        │ FE-Doc    │        │ UX-Doc    │
 └───────────┘        └───────────┘        └───────────┘
```

## Agent Count Summary

| Level | Count | Agents |
|-------|-------|--------|
| Executive | 1 | CEO (Human - Renzo) |
| Board | 3 | Product Owner, Head of Marketing, Auditor |
| Management | 1 | Main PM |
| Backend Cell | 5 | 2 Devs, 1 QA, 1 PM, 1 Documenter |
| Frontend Cell | 5 | 2 Devs, 1 QA, 1 PM, 1 Documenter |
| UX/UI Cell | 4 | 1 Dev, 1 QA, 1 PM, 1 Documenter |
| **Total** | **19** | 18 AI agents + 1 Human CEO |

**Blueprints Created**: 16 (all AI agents)

## Communication Flow

```
Board Level                    Management                    Cells
┌─────────────┐               ┌───────────┐               ┌─────────┐
│ Product     │──requirements─►│           │──priorities──►│ Cell    │
│ Owner       │               │           │               │ PMs     │
└─────────────┘               │  Main PM  │               └────┬────┘
                              │           │◄──status──────────┤
┌─────────────┐               │           │               ┌────▼────┐
│ Head of     │◄──launches────│           │               │ Cell    │
│ Marketing   │               └───────────┘               │ Members │
└─────────────┘                     ▲                     └─────────┘
                                    │
┌─────────────┐                     │
│  Auditor    │────observes all─────┴─────────────────────────────────►
└─────────────┘
```

## Cross-Cell Dependencies

```
UX/UI Cell                    Frontend Cell                 Backend Cell
    │                              │                              │
    │  ──── Designs ──────────►    │                              │
    │                              │  ◄──── APIs ─────────────    │
    │  ◄─── Questions ────────     │                              │
    │                              │  ──── API Needs ─────────►   │
    │  ──── Updates ──────────►    │                              │
    │                              │  ◄──── Endpoints ────────    │
```

## Blueprint Structure

Each blueprint contains:

```yaml
# Identity
id, name, role, team, cell

# System Prompt
- Identity and position
- Core responsibilities
- Core principles
- Detailed workflow
- Communication rules
- Templates and formats
- Example interactions

# Capabilities
- Tools and actions available

# Permissions
- Channel access (read/write)
- Task permissions
- Notification abilities
```

## Role Categories

### Developers (3 types)
| Cell | Focus | Stack | Output |
|------|-------|-------|--------|
| Backend | APIs, services | Python, FastAPI | Endpoints, logic |
| Frontend | UI components | TypeScript, React | Components, pages |
| UX/UI | Design | Figma | Designs, specs |

### QA Engineers (3 types)
| Cell | Focus | Checks |
|------|-------|--------|
| Backend | Functional, security | Code quality, tests, vulnerabilities |
| Frontend | Visual, a11y, cross-browser | Design fidelity, accessibility, browsers |
| UX/UI | Consistency, completeness | Design system, states, accessibility |

### Documenters (3 types)
| Cell | Focus | Output |
|------|-------|--------|
| Backend | API documentation | OpenAPI specs, architecture docs |
| Frontend | Component documentation | Storybook, component docs |
| UX/UI | Design system documentation | Pattern libraries, token docs |

### PMs (4 types)
| Role | Scope | Key Coordination |
|------|-------|------------------|
| BE-PM | Backend cell | Main PM, FE-PM (APIs) |
| FE-PM | Frontend cell | UX-PM (designs), BE-PM (APIs) |
| UX-PM | UX/UI cell | FE-PM (handoffs), Product Owner |
| Main PM | All cells | Board, all Cell PMs |

### Board (3 types)
| Role | Focus | Key Interactions |
|------|-------|------------------|
| Product Owner | What to build | Main PM, CEO |
| Head of Marketing | Go-to-market | Product Owner, CEO |
| Auditor | Quality oversight | Everyone (silently), CEO |

## Testing Blueprints

Test these blueprints with Claude Code:

1. Copy the system prompt section from any blueprint
2. Start a Claude Code session with that prompt
3. Simulate tasks and interactions
4. Note gaps and improvements needed

### Suggested Test Scenarios

**Single Agent**:
- Give a dev agent a task and watch the workflow
- Have a QA agent review completed work
- Have a PM assign and track tasks

**Cell Interaction**:
- Full task lifecycle: PM assigns → Dev works → QA reviews → Doc documents
- Blocker scenario: Dev blocked → PM escalates → Resolution

**Cross-Cell**:
- UX designs → FE implements (with handoff)
- FE needs API → BE provides (with coordination)

**Board Level**:
- Product Owner creates requirements → Main PM distributes
- Head of Marketing plans launch → Coordinates with Product Owner

## Notification Permissions

| Agent | Can Send Notifications? | To Whom? |
|-------|------------------------|----------|
| Developers | ❌ | - |
| QA | ❌ | - |
| Documenters | ❌ | - |
| Cell PMs | ✅ | Own cell + other PMs |
| Main PM | ✅ | Anyone |
| Product Owner | ✅ | Main PM, Head Marketing, CEO |
| Head Marketing | ✅ | Product Owner, Main PM, CEO |
| Auditor | ✅ | Anyone (sparingly) |

## Channel Access Summary

| Channel | Cell Members | Cell PMs | Main PM | Board | Auditor |
|---------|--------------|----------|---------|-------|---------|
| #backend-cell | BE only (rw) | BE (rw) | read | - | read |
| #frontend-cell | FE only (rw) | FE (rw) | read | - | read |
| #uxui-cell | UX only (rw) | UX (rw) | read | - | read |
| #dev-all | devs (rw) | read | read | - | read |
| #qa-all | qa (rw) | read | read | - | read |
| #pm-all | - | rw | rw | - | read |
| #main-pm-board | - | - | rw | rw | read |
| #board-private | - | - | read | rw | rw |
| #announcements | read | read | rw | rw | read |
| #all-hands | rw | rw | rw | rw | rw |

## What's Next?

With all blueprints complete, suggested next steps:

1. **Test the blueprints** - Simulate agents with these prompts
2. **Create task templates** - Build the `.tasks/` structure
3. **Build data models** - SQLAlchemy/Pydantic from blueprint concepts
4. **Implement Messaging API** - Enable actual agent communication
5. **Deploy first cell** - Start with Backend cell as pilot
