import { redirect } from "next/navigation";

// Products merged into the Workstation tab shell — see
// (dashboard)/workstation/page.tsx.
export default function ProductsPage() {
  redirect("/workstation?tab=products");
}
