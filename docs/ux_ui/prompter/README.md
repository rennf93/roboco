# Prompter UX Design Specification

**Version:** 1.0  
**Date:** 2026-06-06  
**Owner:** UX/UI Cell (ux-dev-1)  
**Status:** Design Complete → Awaiting Implementation (ux-dev-2)

---

## Overview

Prompter is a new `/prompter` page for **conversational task authoring**. Instead of manually filling a rigid task-creation form, users chat with an LLM to co-draft a task. The LLM translates natural-language intent into structured task fields (title, description, acceptance criteria, team, priority, etc.). Before any task enters the dev pipeline, the user must explicitly review and confirm every field.

This spec covers the complete interaction model, state machine, confirmation flow, draft-review panel, accessibility requirements, and component interfaces for implementation.

---

## Design Principles

1. **Human-in-the-loop, always.** The LLM is an assistant, not an author. Every AI-generated field must be visibly labeled and editable. The user has final veto power.
2. **Unmistakable confirmation.** The confirmation gate is a deliberate UX moment — not a checkbox, not a subtle banner. The user must stop, read, and decide.
3. **Transparent drafting.** Users must be able to tell at a glance which fields the LLM suggested and which they (or a previous human) edited.
4. **Progressive disclosure.** The interface starts simple (chat) and reveals complexity only when needed (draft review, advanced fields).
5. **Reusable history.** Past prompts and their resulting tasks are persisted as reusable templates. History is first-class, not an afterthought.

---

## Navigation Placement

Prompter lives in the **Work Management** section of the sidebar, between **Kanban** and **Projects**.

```
Work Management
├── Tasks
├── Kanban
├── Prompter  ← NEW (icon: Sparkles / Wand2)
```

**Rationale:** Prompter is a task-creation entry point, not a reference tool. Placing it next to Tasks and Kanban reinforces its role in the work-management workflow. The `Sparkles` or `Wand2` icon (from `lucide-react`) signals AI assistance without being overly playful.

---

## Related Documents

| Document | Purpose |
|----------|---------|
| [`state-machine.md`](state-machine.md) | All prompter states, transitions, error handling, loading states |
| [`confirmation-flow.md`](confirmation-flow.md) | The confirmation gate, draft-review panel, inline editing patterns |
| [`accessibility.md`](accessibility.md) | WCAG 2.1 AA compliance, keyboard navigation, screen-reader behavior |
| [`component-interfaces.md`](component-interfaces.md) | TypeScript prop interfaces for ux-dev-2 implementation |

---

## Quick Reference: State Machine

```
[EMPTY] --user sends first message--> [CHATTING]
[CHATTING] --user clicks "Generate Draft" or LLM offers draft--> [DRAFTING]
[DRAFTING] --LLM returns structured task--> [REVIEWING]
[REVIEWING] --user confirms draft--> [CONFIRMING]
[CONFIRMING] --user confirms launch--> [LAUNCHED] → task created, redirect to task detail
[REVIEWING] --user rejects / wants changes--> [CHATTING] (with context preserved)
[CHATTING] --user opens sidebar--> [HISTORY_VISIBLE]
[HISTORY_VISIBLE] --user selects template--> [CHATTING] (template injected)
```

See [`state-machine.md`](state-machine.md) for full details on each state.

---

## Risk Register

| Risk | Mitigation |
|------|------------|
| Design specs too abstract for ux-dev-2 | Component interfaces include concrete prop types, pseudo-code state transitions, and reference existing component file paths |
| Confirmation gate feels obstructive | Spec includes two variants (modal gate vs inline review banner) with guidance on context-of-use |
| LLM-suggested vs human-edited differentiation fails color-only WCAG rule | Accessibility doc mandates icons + labels + patterns in addition to color |

---

## Acceptance Criteria Summary

- [x] All prompter states documented with layout, transitions, and error handling
- [x] Unmistakable confirmation gate specified (not a subtle checkbox)
- [x] Draft-review panel makes LLM-suggested fields editable and clearly labeled
- [x] Visual differentiation between AI-generated and human-edited content defined
- [x] Accessibility documentation covers WCAG 2.1 AA for all states
- [x] Component prop interfaces provided for implementation handoff
- [x] Navigation placement and icon specified
