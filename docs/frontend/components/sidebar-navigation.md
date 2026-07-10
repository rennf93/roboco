# Sidebar Navigation

## Overview

The sidebar navigation in `panel/src/components/layout/sidebar.tsx` is a single flat list of navigation links. A visual `Separator` divider appears once, between the main navigation list and the footer group (Business + settings). This structure appears in both the desktop sidebar (with optional collapse to icon-only rail) and the mobile Sheet drawer.

## Navigation Items

The navigation list contains 14 items in this exact order:

1. **Overview** (`/overview`) – Command center, high-level status
2. **Task Assistant** (`/prompter`) – AI-assisted task creation/coaching
3. **Tasks** (`/tasks`) – Task list and detail view
4. **Kanban** (`/kanban`) – Kanban board view
5. **Git** (`/git`) – Git/CI/CD integration view
6. **Projects** (`/projects`) – Repository and project management
7. **Products** (`/products`) – Product configuration
8. **Social** (`/social`) – Social media tracking and insights
9. **Knowledge Base** (`/knowledge-base`) – Organizational RAG/learnings
10. **A2A** (`/a2a`) – Agent-to-agent live messaging
11. **Agents** (`/agents`) – AI agent roster and status
12. **Journals** (`/journals`) – Agent journal entries and reflections
13. **Auditor** (`/auditor`) – Quality gate dashboard
14. **Metrics** (`/metrics`) – System performance and delivery metrics

## Footer Items

After a single `Separator` divider, the footer contains three links:

- **Business** (`/business`) – Business metrics and planning
- **AI Providers** (`/settings/ai-providers`) – LLM provider configuration
- **Settings** (`/settings`) – General application settings

## Key Structural Changes

### Navigation Flattened to Single List
Navigation previously organized into six logical groups (Dashboard, Work Management, Development, Team & Reference, History, System) with a `Separator` after each group. All items now appear in a single flat `navItems` array without per-item grouping dividers.

### Business Moved to Footer
The **Business** entry moved from the Dashboard group (first group) to the footer items, positioned immediately before AI Providers. This visually separates business/administrative concerns from the core workflow links.

### Single Separator Between Nav and Footer
A single `Separator` now renders once, between the main navigation list and the footer group. In the old structure, there were five dividers (one after each of the first five groups). The new structure places this divider at a clear boundary between primary workflow and secondary/settings links.

### A2A Entry Rename
The `/a2a` entry label changed from **"A2A Live"** to **"A2A"** for brevity. The route, icon (Radio), and functionality remain unchanged.

### Notifications Removed from Sidebar
The **Notifications** entry (previously in the System group) no longer appears in the sidebar or mobile drawer. Notifications remain accessible via:
- The **NotificationBell** icon in the header (`panel/src/components/header/notification-bell.tsx`)
- The **"View All Notifications"** link within the notification popover
- The `/notifications` route is still available and untouched

## Visual Behavior

### The Separator Divider
A single `Separator` component renders between the main navigation list (`SidebarNav`) and the footer links (`SidebarFooter`). The separator appears identically in both expanded and collapsed states:
- **Expanded sidebar:** Divider appears with normal width (`my-2` margin)
- **Collapsed sidebar (icon-only rail):** Divider still renders in the same position
- No additional dividers appear within the navigation list or within the footer group

### Collapsed State
When the sidebar is collapsed (icon-only mode):
- The separator continues to render between nav and footer
- Link labels are hidden
- Icon titles appear as tooltips (`title` attribute)
- Link layout uses `justify-center px-2` for icon centering

### Mobile
The mobile Sheet drawer reuses the same `SidebarNav` and `SidebarFooter` components with the separator between them, so appearance and behavior match the desktop sidebar in both expanded and collapsed states.

## Data Structure

Navigation items are exported as a single flat `navItems` array:

```typescript
export const navItems = [
  { title: "Overview", href: "/overview", icon: LayoutDashboard },
  { title: "Task Assistant", href: "/prompter", icon: Sparkles },
  // ... 12 more items in exact order ...
  { title: "Metrics", href: "/metrics", icon: Activity },
];
```

Footer items are defined separately:

```typescript
const footerItems = [
  { title: "Business", href: "/business", icon: Building2 },
  { title: "AI Providers", href: "/settings/ai-providers", icon: Cpu },
  { title: "Settings", href: "/settings", icon: Settings },
];
```

**Key points:**
- `navItems` order is stable and part of the acceptance criteria — changing the order requires updating tests and design specs
- `footerItems` are rendered after a `Separator` divider by the `SidebarFooter` component
- Each item includes `title` (display label), `href` (route), and `icon` (Lucide icon component)
- No internal grouping or comments in the structure — the flat order is the source of truth for navigation organization

## Testing

Sidebar behavior is tested in `panel/src/components/layout/__tests__/sidebar.test.tsx`. The test suite covers:

**Navigation (`navItems`) tests:**
- `navItems` is a single flat array in the exact expected order (Overview → Metrics)
- Business is not in `navItems`

**SidebarNav component tests:**
- No dividers within the nav list (zero separators)
- All nav items render as links in the correct order
- Collapsed mode hides labels while preserving layout

**SidebarFooter component tests:**
- Footer items render in order: Business → AI Providers → Settings
- Exactly one `Separator` divides nav from footer (in both expanded and collapsed states)
- Collapsed mode hides labels while preserving layout
