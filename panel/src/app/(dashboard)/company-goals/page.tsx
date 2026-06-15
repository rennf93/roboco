"use client";

import { CompanyGoalsCard } from "@/components/company-goals/company-goals-card";

export default function CompanyGoalsPage() {
  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Company Goals</h1>
        <p className="text-muted-foreground">
          The organization&apos;s charter — north star, objectives, constraints,
          and operating policy that steer every agent&apos;s work.
        </p>
      </div>
      <CompanyGoalsCard />
    </div>
  );
}
