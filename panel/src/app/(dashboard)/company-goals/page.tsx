import { redirect } from "next/navigation";

/**
 * /company-goals is now consolidated into /business?tab=goals.
 * This permanent redirect keeps existing bookmarks / links working.
 */
export default function CompanyGoalsRedirectPage() {
  redirect("/business?tab=goals");
}
