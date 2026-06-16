"use client";

import { useQuery } from "@tanstack/react-query";
import { cockpitApi } from "@/lib/api/cockpit";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { TrendingUp } from "lucide-react";

interface StrategySignalsPanelProps {
  className?: string;
}

export function StrategySignalsPanel({ className }: StrategySignalsPanelProps) {
  const { data: signalsData, isLoading } = useQuery({
    queryKey: ["cockpit", "signals"],
    queryFn: () => cockpitApi.signals(),
    refetchInterval: 30000,
  });

  const signals = signalsData ?? [];

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TrendingUp className="h-5 w-5" />
          Strategy Signals
        </CardTitle>
        <CardDescription>Live signals from the strategy engine</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : signals.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            <TrendingUp className="h-12 w-12 mx-auto mb-2 opacity-50" />
            <p>No strategy signals right now</p>
          </div>
        ) : (
          <div className="space-y-3">
            {signals.map((signal, index) => (
              <div
                key={index}
                className="flex items-start gap-3 p-4 border rounded-lg hover:bg-muted/50 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge variant="outline" className="text-xs">
                      {signal.kind}
                    </Badge>
                  </div>
                  <p className="font-medium text-sm">{signal.summary}</p>
                  {signal.detail && (
                    <p className="text-sm text-muted-foreground mt-1">
                      {signal.detail}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
