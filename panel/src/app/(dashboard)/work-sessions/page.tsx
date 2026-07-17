import { redirect } from "next/navigation";

// The work-sessions surface moved under /git as its "Work Sessions" tab.
export default function WorkSessionsPage() {
  redirect("/git?tab=sessions");
}
