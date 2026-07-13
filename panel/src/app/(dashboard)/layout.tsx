import { Suspense } from "react";
import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { BottomTabBar } from "@/components/layout/bottom-tab-bar";
import { ScrollRestoration } from "@/components/scroll-restoration";
import { RateLimitBanner } from "@/components/rate-limit/rate-limit-banner";
import { AutoRefreshDriver } from "@/components/providers/auto-refresh-driver";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // h-dvh (not h-screen/100vh): mobile Safari's dynamic toolbar resizes the
    // viewport, and 100vh doesn't track that — dvh does.
    <div className="flex h-dvh overflow-hidden">
      <AutoRefreshDriver />
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <RateLimitBanner />
        {/* pb-20 clears the fixed BottomTabBar on mobile; md+ has no bar. */}
        <main className="flex-1 overflow-auto bg-muted/30 p-4 pb-20 md:p-6">
          <Suspense fallback={null}>
            <ScrollRestoration />
          </Suspense>
          {children}
        </main>
      </div>
      <BottomTabBar />
    </div>
  );
}
