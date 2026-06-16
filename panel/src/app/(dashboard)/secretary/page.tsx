import { redirect } from "next/navigation";

/**
 * /secretary is now consolidated into /business?tab=secretary.
 * This permanent redirect keeps existing bookmarks / links working.
 */
export default function SecretaryRedirectPage() {
  redirect("/business?tab=secretary");
}
