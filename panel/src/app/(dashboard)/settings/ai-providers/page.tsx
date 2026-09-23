import { AIRoutingCard } from "@/components/settings/ai-routing-card";
import { DecisionsStatusBanner } from "@/components/settings/decisions-status-banner";

export default function AIProvidersPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">AI Providers</h1>
        <p className="text-muted-foreground">
          Pick how roboco agents authenticate and which model each one runs on.
        </p>
      </div>

      {/* Warns when the Decisions OpenRouter fallback tier is opted in without
          a stored OpenRouter key; renders nothing otherwise. */}
      <DecisionsStatusBanner />

      <AIRoutingCard />
    </div>
  );
}
