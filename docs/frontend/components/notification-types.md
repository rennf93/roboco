# Notification Types Reference

The RoboCo panel renders five core **coordination-event notification types** — formal signals between agents tied to task lifecycle events. Each type has a visual identity (icon + color) and a semantic meaning. All types carry optional deep-links to related tasks.

## The 5 Coordination-Event Types

### 1. TASK_ASSIGNMENT
**Icon:** ListTodo (green) **Use case:** An agent has been assigned a new task. **When sent:** Via `NotificationService.send_task_assignment` when a PM assigns work. **Related task:** Usually carries `related_task_id` linking to the assigned task.

### 2. BLOCKER_ESCALATION
**Icon:** AlertTriangle (red) **Use case:** A developer is blocked and has escalated the issue to the PM. **When sent:** Via `NotificationDeliveryService.escalate_and_notify` when an agent calls `i_am_blocked`. **Related task:** Links to the task that is blocked.

### 3. REVIEW_REQUEST
**Icon:** Check (purple) **Use case:** QA has been asked to review a developer's work. **When sent:** Via `NotificationService.send_qa_ready` when a dev submits `i_am_done`. **Related task:** Links to the task under review.

### 4. DOCUMENTATION_REQUEST
**Icon:** Info (blue) **Use case:** A documenter has been asked to write docs for a code change. **When sent:** Via `NotificationService.send_docs_ready` when QA passes a task. **Related task:** Links to the task whose code needs documenting.

### 5. APPROVAL *(New in this release)*
**Icon:** ShieldCheck (emerald) **Use case:** A Board member (Product Owner, Head of Marketing, or Main PM) is requested to approve or provide feedback on escalated work. **When sent:** Via `NotificationDeliveryService.notify_ceo_of_escalation` when a task escalates to the Board. **Related task:** Links to the task awaiting approval. **Backend reference:** Matches `roboco/models/base.py` `NotificationType.APPROVAL`.

## Implementation Details

All types are defined in `panel/src/types/index.ts` under the `NotificationType` enum:

```typescript
export enum NotificationType {
  TASK_ASSIGNMENT = "task_assignment",
  BLOCKER_ESCALATION = "blocker_escalation",
  REVIEW_REQUEST = "review_request",
  DOCUMENTATION_REQUEST = "documentation_request",
  APPROVAL = "approval", // Board-level approval requests (PO/HM/Main PM)
  // ... other types (ALERT, BROADCAST, etc.)
}
```

### Icon Mapping
Icons are rendered in `panel/src/app/(dashboard)/notifications/page.tsx` via the `typeIcons` Record — a TypeScript-enforced exhaustive mapping that ensures every enum member has a visual representation:

```typescript
const typeIcons: Record<NotificationType, React.ReactNode> = {
  [NotificationType.TASK_ASSIGNMENT]: <ListTodo className="h-4 w-4 text-green-500" />,
  [NotificationType.BLOCKER_ESCALATION]: <AlertTriangle className="h-4 w-4 text-red-500" />,
  [NotificationType.REVIEW_REQUEST]: <Check className="h-4 w-4 text-purple-500" />,
  [NotificationType.DOCUMENTATION_REQUEST]: <Info className="h-4 w-4 text-blue-500" />,
  [NotificationType.APPROVAL]: <ShieldCheck className="h-4 w-4 text-emerald-500" />,
  // ...
};
```

TypeScript's `Record<K, V>` type ensures that if a new `NotificationType` enum member is added without a corresponding icon entry, the code will not compile — preventing the "missing icon" bug at build time.

### Deep-Linking to Tasks
When a notification carries a `related_task_id`, the notifications page renders a Next.js `Link` component:

```tsx
{notification.related_task_id && (
  <Link href={`/tasks/${notification.related_task_id}`} className="text-primary hover:underline">
    Task #{notification.related_task_id.substring(0, 8)}
  </Link>
)}
```

This allows agents to navigate directly from the notification inbox to the related task's detail view.

## Testing

The 5 coordination-event types are covered by component tests in `panel/src/app/(dashboard)/notifications/__tests__/page.test.tsx`:

1. **Deep-link test:** Verifies that a TASK_ASSIGNMENT notification renders a working `<Link>` to `/tasks/{task_id}`.
2. **Icon test:** Verifies that all 5 types render and are queryable by their subject text.

## Adding a New Coordination-Event Type

To add a new coordination-event notification type:

1. Add the enum member to `NotificationType` in `panel/src/types/index.ts`
2. Add a corresponding icon entry to the `typeIcons` Record in `notifications/page.tsx`
3. Add a test case to the component test file
4. Update backend `roboco/models/base.py` `NotificationType` to match
5. Wire the notification send/delivery method in the backend service

The TypeScript exhaustiveness check will catch any missing icon entry at build time.
