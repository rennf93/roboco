import { notFound } from "next/navigation";

/**
 * /cockpit has been retired. Navigating here returns a 404.
 */
export default function CockpitPage() {
  notFound();
}
