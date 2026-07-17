import { redirect } from "next/navigation";

// Projects merged into the Workstation tab shell — see
// (dashboard)/workstation/page.tsx.
export default function ProjectsPage() {
  redirect("/workstation?tab=projects");
}
