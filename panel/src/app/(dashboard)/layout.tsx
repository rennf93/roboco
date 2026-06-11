import { Suspense } from "react";
import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { ScrollRestoration } from "@/components/scroll-restoration";
import { RateLimitBanner } from "@/components/rate-limit/rate-limit-banner";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <RateLimitBanner />
        <main className="flex-1 overflow-auto bg-muted/30 p-6">
          <Suspense fallback={null}>
            <ScrollRestoration />
          </Suspense>
          {children}
        </main>
      </div>
    </div>
  );
}
