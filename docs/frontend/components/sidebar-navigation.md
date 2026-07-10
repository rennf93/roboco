# Sidebar Navigation

## Overview

The sidebar navigation in `panel/src/components/layout/sidebar.tsx` organizes the RoboCo panel's main navigation links into six logical groups, with a visual divider (`Separator`) between each group. This structure appears in both the desktop sidebar (with optional collapse to icon-only rail) and the mobile Sheet drawer.

## Navigation Groups

The six groups organize work by domain:

### 1. Dashboard
- **Overview** (`/overview`) – Command center, high-level status
- **Business** (`/business`) – Business metrics and planning
- **Social** (`/social`) – Social media tracking and insights

### 2. Work Management
- **Tasks** (`/tasks`) – Task list and detail view
- **Kanban** (`/kanban`) – Kanban board view
- **Task Assistant** (`/prompter`) – AI-assisted task creation/coaching

### 3. Development
- **Projects** (`/projects`) – Repository and project management
- **Products** (`/products`) – Product configuration
- **Git** (`/git`) – Git/CI/CD integration view

### 4. Team & Reference
- **Agents** (`/agents`) – AI agent roster and status
- **Knowledge Base** (`/knowledge-base`) – Organizational RAG/learnings
- **Auditor** (`/auditor`) – Quality gate dashboard

### 5. History
- **A2A** (`/a2a`) – Agent-to-agent live messaging (renamed from "A2A Live")
- **Journals** (`/journals`) – Agent journal entries and reflections

### 6. System
- **Metrics** (`/metrics`) – System performance and delivery metrics
- (Notifications no longer appear here; see below)

## Key Changes

### A2A Entry Rename
The `/a2a` entry label changed from **"A2A Live"** to **"A2A"** for brevity. The route, icon (Radio), and functionality remain unchanged.

### Notifications Removed from Sidebar
The **Notifications** entry (previously `/notifications` in System) no longer appears in the sidebar or mobile drawer. Notifications remain accessible via:
- The **NotificationBell** icon in the header (`panel/src/components/header/notification-bell.tsx`)
- The **"View All Notifications"** link within the notification popover
- The `/notifications` route is still available and untouched

## Visual Behavior

### Dividers
A `Separator` component renders between each consecutive group (not before the first group). The dividers respect the collapsed state:
- **Expanded sidebar:** Divider appears with normal width (`my-2` margin)
- **Collapsed sidebar (icon-only rail):** Divider still renders, preserving visual grouping

### Collapsed State
When the sidebar is collapsed (icon-only mode):
- Group structure is preserved (dividers still render)
- Link labels are hidden
- Icon titles appear as tooltips (`title` attribute)
- Link layout uses `justify-center px-2` for icon centering

### Mobile
The mobile Sheet drawer reuses the same `SidebarNav` component, so grouping and dividers appear identically on both surfaces.

## Data Structure

Navigation items are organized as `navGroups` (array of arrays), then flattened into `navItems` for any code needing a flat list:

```typescript
const navGroups = [
  [/* Dashboard group */],
  [/* Work Management group */],
  // ... etc
];

export const navItems = navGroups.flat();
```

This dual structure:
- Keeps the grouping stable and self-documenting (comment-labeled sections)
- Avoids couples UI concern (dividers) to individual items
- Maintains item order as part of the acceptance criteria

## Testing

Sidebar behavior is tested in `panel/src/components/layout/__tests__/sidebar.test.tsx`:
- Divider count (5 dividers for 6 groups)
- A2A label ("A2A", not "A2A Live")
- Notifications absence
- Item order preservation
- Collapsed state layout correctness
