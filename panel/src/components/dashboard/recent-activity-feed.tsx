"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Activity as ActivityIcon, ArrowRight } from "lucide-react";
import { ActivityItem, Activity } from "./activity-item";
import Link from "next/link";

interface RecentActivityFeedProps {
  activities: Activity[] | undefined;
  isLoading: boolean;
}

export function RecentActivityFeed({ activities, isLoading }: RecentActivityFeedProps) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-lg flex items-center gap-2">
          <ActivityIcon className="h-5 w-5" />
          Recent Activity
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        ) : !activities || activities.length === 0 ? (
          <div className="text-center py-4 text-muted-foreground text-sm">
            <ActivityIcon className="h-8 w-8 mx-auto mb-2 opacity-50" />
            No recent activity
          </div>
        ) : (
          <ScrollArea className="h-[280px] pr-4">
            <div className="divide-y">
              {activities.slice(0, 10).map((activity) => (
                <ActivityItem key={activity.id} activity={activity} />
              ))}
            </div>
          </ScrollArea>
        )}
        <div className="mt-4 pt-3 border-t">
          <Link href="/notifications">
            <Button variant="ghost" size="sm" className="w-full">
              View Full Activity
              <ArrowRight className="h-4 w-4 ml-2" />
            </Button>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
