import { TaskChatView } from "@/components/task-chat/task-chat-view";

/**
 * /task-chat — Chat-based task creation page.
 *
 * Presents a three-panel layout:
 *   - Conversation history sidebar (left)
 *   - Chat panel with model selector (center)
 *   - Structured task draft panel (right)
 */
export default function TaskChatPage() {
  return <TaskChatView />;
}
