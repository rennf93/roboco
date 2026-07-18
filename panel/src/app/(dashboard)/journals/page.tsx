import { redirect } from "next/navigation";

// Journals merged into the Agents hub as its Journals tab (CEO decision,
// wave 11) — this route now just forwards old links/bookmarks.
export default function JournalsPage() {
  redirect("/agents?tab=journals");
}
