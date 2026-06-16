import { redirect } from "next/navigation";

/**
 * /pitches is now consolidated into /business?tab=pitches.
 * This permanent redirect keeps existing bookmarks / links working.
 */
export default function PitchesRedirectPage() {
  redirect("/business?tab=pitches");
}
