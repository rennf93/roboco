import { AIRoutingCard } from "@/components/settings/ai-routing-card";
import { ProviderKeyCard } from "@/components/settings/provider-key-card";

export default function AIProvidersPage() {
  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">AI Providers</h1>
        <p className="text-muted-foreground">
          Configure how RoboCo agents authenticate and which model each one runs
          on. Add your API keys below instead of relying on Docker-mounted
          credentials.
        </p>
      </div>

      {/* API Key Management — enter keys for Anthropic, OpenAI, Ollama, etc. */}
      <ProviderKeyCard />

      {/* Routing configuration — toggle between modes, assign models to agents. */}
      <AIRoutingCard />
    </div>
  );
}
