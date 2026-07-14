"use client";

import { Task } from "@/types";
import { useTaskCollisionMap } from "@/hooks/use-tasks";
import type { CollisionMap, CollisionSibling } from "@/lib/api/tasks";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { GitBranch, AlertTriangle } from "lucide-react";

interface TabCollisionProps {
  task: Task;
}

// Status → tailwind badge class (a small inline map; no shared helper exists
// across the task-detail tabs, so this mirrors tab-findings' inline maps).
const STATUS_CLASS: Record<string, string> = {
  pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  claimed:
    "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
  in_progress:
    "bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-300",
  awaiting_qa:
    "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300",
  awaiting_documentation:
    "bg-cyan-100 text-cyan-700 dark:bg-cyan-900 dark:text-cyan-300",
  awaiting_pr_review:
    "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300",
  awaiting_pm_review:
    "bg-teal-100 text-teal-700 dark:bg-teal-900 dark:text-teal-300",
  awaiting_ceo_approval:
    "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300",
  needs_revision:
    "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
  completed:
    "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
  cancelled:
    "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-500",
  blocked:
    "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
  paused: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
  verifying:
    "bg-violet-100 text-violet-700 dark:bg-violet-900 dark:text-violet-300",
  backlog: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-500",
};

function SiblingCard({ sib }: { sib: CollisionSibling }) {
  return (
    <Card>
      <CardContent className="pt-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <code className="text-xs font-mono text-muted-foreground">
            {sib.id}
          </code>
          {sib.title && (
            <span className="text-sm font-medium truncate">{sib.title}</span>
          )}
          <Badge
            className={STATUS_CLASS[sib.status] ?? STATUS_CLASS.pending}
          >
            {sib.status}
          </Badge>
          {sib.branch_name && (
            <code className="text-xs text-muted-foreground flex items-center gap-1">
              <GitBranch className="h-3 w-3" />
              {sib.branch_name}
            </code>
          )}
          {sib.pr_number != null && (
            <Badge variant="outline">#{sib.pr_number}</Badge>
          )}
          {sib.adds_migration && (
            <Badge variant="outline" className="text-amber-700">
              +migration
            </Badge>
          )}
          {sib.touches_shared && (
            <Badge variant="outline" className="text-orange-700">
              shared
            </Badge>
          )}
          {sib.sequence != null && (
            <span className="ml-auto text-xs text-muted-foreground">
              seq {sib.sequence}
            </span>
          )}
        </div>

        {sib.intends_to_touch.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {sib.intends_to_touch.map((g) => (
              <code
                key={g}
                className={
                  "text-xs rounded px-1.5 py-0.5 " +
                  (sib.overlap.includes(g)
                    ? "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300"
                    : "bg-muted text-muted-foreground")
                }
              >
                {g}
              </code>
            ))}
          </div>
        )}

        {sib.overlap.length > 0 && (
          <p className="text-xs text-muted-foreground">
            <AlertTriangle className="inline h-3 w-3 mr-1" />
            Overlaps your declared surface on{" "}
            {sib.overlap.join(", ")}.
          </p>
        )}

        {sib.undeclared.length > 0 && (
          <div className="rounded border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 p-2 text-xs">
            <span className="text-amber-700 dark:text-amber-300 font-medium">
              Drift — touched but not declared:
            </span>
            <div className="mt-1 flex flex-wrap gap-1">
              {sib.undeclared.map((f) => (
                <code
                  key={f}
                  className="bg-amber-100 dark:bg-amber-900 text-amber-800 dark:text-amber-200 rounded px-1.5 py-0.5"
                >
                  {f}
                </code>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function TabCollision({ task }: TabCollisionProps) {
  const { data, isLoading } = useTaskCollisionMap(task.id);

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  // No parent = a root; no collision siblings by construction.
  if (!data || data.parent_task_id == null) {
    return (
      <div className="py-12 text-center text-muted-foreground">
        <GitBranch className="mx-auto mb-4 h-12 w-12 opacity-50" />
        <p>No collision map — this task has no parent.</p>
        <p className="mt-2 text-sm">
          Roots and standalone tasks have no siblings to collide with.
        </p>
      </div>
    );
  }

  const siblings = data.siblings;

  if (siblings.length === 0) {
    return (
      <div className="space-y-4">
        <DeclaredSurfaceCard data={data} />
        <div className="py-8 text-center text-muted-foreground">
          <GitBranch className="mx-auto mb-4 h-12 w-12 opacity-50" />
          <p>No colliding siblings.</p>
          <p className="mt-2 text-sm">
            None of this task&apos;s siblings share its declared surface or
            migration chain.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <DeclaredSurfaceCard data={data} />
      <div>
        <h3 className="text-sm font-semibold mb-3">
          {siblings.length} colliding sibling{siblings.length > 1 ? "s" : ""}
        </h3>
        <div className="space-y-3">
          {siblings.map((sib) => (
            <SiblingCard key={sib.id} sib={sib} />
          ))}
        </div>
      </div>
    </div>
  );
}

function DeclaredSurfaceCard({ data }: { data: CollisionMap }) {
  const hasSurface =
    data.intends_to_touch.length > 0 ||
    data.adds_migration ||
    data.touches_shared;
  return (
    <Card>
      <CardContent className="pt-4 space-y-2">
        <h3 className="text-sm font-semibold">Declared surface</h3>
        {hasSurface ? (
          <>
            {data.intends_to_touch.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {data.intends_to_touch.map((g) => (
                  <code
                    key={g}
                    className="text-xs bg-muted text-muted-foreground rounded px-1.5 py-0.5"
                  >
                    {g}
                  </code>
                ))}
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              {data.adds_migration && (
                <Badge variant="outline" className="text-amber-700">
                  adds migration
                </Badge>
              )}
              {data.touches_shared && (
                <Badge variant="outline" className="text-orange-700">
                  touches shared
                </Badge>
              )}
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            This task declared no collision surface.
          </p>
        )}
      </CardContent>
    </Card>
  );
}