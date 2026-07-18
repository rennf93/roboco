import { redirect } from "next/navigation";

// A2A merged into the Agents hub as its Conversations tab (CEO decision,
// wave 9) — this route now just forwards old links/bookmarks.
export default function A2APage() {
  redirect("/agents?tab=conversations");
}
