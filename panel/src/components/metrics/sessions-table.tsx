"use client";

import { useState, useMemo } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ResponsiveTable,
  ResponsiveTableCardList,
  ResponsiveTableCard,
  ResponsiveTableCardRow,
} from "@/components/ui/responsive-table";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { ChevronUp, ChevronDown } from "lucide-react";
import type { UsageSession } from "@/types";

const PAGE_SIZE = 10;

type SortKey = keyof Pick<
  UsageSession,
  | "agent_slug"
  | "started_at"
  | "total_tokens"
  | "tokens_input"
  | "tokens_output"
  | "tokens_cache"
  | "cost"
  | "model"
>;

type SortDir = "asc" | "desc";

interface Column {
  key: SortKey;
  label: string;
  tip: string;
}

const COLUMNS: Column[] = [
  { key: "agent_slug", label: "Agent", tip: "The agent slug that ran this session — click to sort" },
  { key: "model", label: "Model", tip: "Claude/Grok model used for this session — click to sort" },
  {
    key: "started_at",
    label: "Started",
    tip: "Session start time, local timezone — click to sort oldest/newest",
  },
  {
    key: "total_tokens",
    label: "Total",
    tip: "Input + output + cache tokens combined — click to sort",
  },
  {
    key: "tokens_input",
    label: "Input",
    tip: "Prompt tokens sent to the model — click to sort",
  },
  {
    key: "tokens_output",
    label: "Output",
    tip: "Completion tokens returned by the model — click to sort",
  },
  {
    key: "tokens_cache",
    label: "Cache",
    tip: "Tokens served from Anthropic's prompt cache — click to sort",
  },
  {
    key: "cost",
    label: "Cost",
    tip: "Provider-priced dollar cost (local/Ollama sessions are $0) — click to sort",
  },
];

function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtK(n: number): string {
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
  return String(n);
}

interface SessionsTableProps {
  data: UsageSession[] | undefined;
  isLoading: boolean;
}

export function SessionsTable({ data, isLoading }: SessionsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("started_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [page, setPage] = useState(0);

  const sorted = useMemo(() => {
    const rows = [...(data ?? [])];
    rows.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      const cmp =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av).localeCompare(String(bv));
      return sortDir === "asc" ? cmp : -cmp;
    });
    return rows;
  }, [data, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const visible = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
    setPage(0);
  }

  function SortIcon({ col }: { col: SortKey }) {
    if (sortKey !== col)
      return <ChevronUp className="h-3 w-3 opacity-30 ml-1 inline" />;
    return sortDir === "asc" ? (
      <ChevronUp className="h-3 w-3 ml-1 inline" />
    ) : (
      <ChevronDown className="h-3 w-3 ml-1 inline" />
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <HelpTip label="The most recent agent spawn sessions with their token/cost breakdown — sortable, 10 per page">
          <CardTitle className="text-base">Recent Sessions</CardTitle>
        </HelpTip>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: PAGE_SIZE }).map((_, i) => (
              <Skeleton key={i} className="h-8" />
            ))}
          </div>
        ) : (
          <>
            <ResponsiveTable
              table={
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        {COLUMNS.map((col) => (
                          <TableHead
                            key={col.key}
                            className="cursor-pointer select-none text-xs whitespace-nowrap"
                            onClick={() => toggleSort(col.key)}
                          >
                            <HelpTip label={col.tip}>
                              <span>
                                {col.label}
                                <SortIcon col={col.key} />
                              </span>
                            </HelpTip>
                          </TableHead>
                        ))}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {visible.length === 0 ? (
                        <TableRow>
                          <TableCell
                            colSpan={COLUMNS.length}
                            className="text-center text-muted-foreground text-sm py-8"
                          >
                            No sessions recorded yet
                          </TableCell>
                        </TableRow>
                      ) : (
                        visible.map((s) => (
                          <TableRow key={s.id}>
                            <TableCell className="text-xs font-medium">
                              {s.agent_slug}
                            </TableCell>
                            <TableCell className="text-xs">{s.model}</TableCell>
                            <TableCell className="text-xs">
                              {formatTime(s.started_at)}
                            </TableCell>
                            <TableCell className="text-xs">
                              {fmtK(s.total_tokens)}
                            </TableCell>
                            <TableCell className="text-xs">
                              {fmtK(s.tokens_input)}
                            </TableCell>
                            <TableCell className="text-xs">
                              {fmtK(s.tokens_output)}
                            </TableCell>
                            <TableCell className="text-xs">
                              {fmtK(s.tokens_cache)}
                            </TableCell>
                            <TableCell className="text-xs">
                              ${s.cost.toFixed(4)}
                            </TableCell>
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </div>
              }
              cards={
                visible.length === 0 ? (
                  <p className="py-8 text-center text-sm text-muted-foreground">
                    No sessions recorded yet
                  </p>
                ) : (
                  <ResponsiveTableCardList>
                    {visible.map((s) => (
                      <ResponsiveTableCard key={s.id}>
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm font-medium">
                            {s.agent_slug}
                          </span>
                          <Badge variant="outline" className="shrink-0 text-xs">
                            {s.model}
                          </Badge>
                        </div>
                        <div className="mt-3 divide-y">
                          <ResponsiveTableCardRow label="Started">
                            {formatTime(s.started_at)}
                          </ResponsiveTableCardRow>
                          <ResponsiveTableCardRow label="Total">
                            {fmtK(s.total_tokens)}
                          </ResponsiveTableCardRow>
                          <ResponsiveTableCardRow label="Input">
                            {fmtK(s.tokens_input)}
                          </ResponsiveTableCardRow>
                          <ResponsiveTableCardRow label="Output">
                            {fmtK(s.tokens_output)}
                          </ResponsiveTableCardRow>
                          <ResponsiveTableCardRow label="Cache">
                            {fmtK(s.tokens_cache)}
                          </ResponsiveTableCardRow>
                          <ResponsiveTableCardRow label="Cost">
                            ${s.cost.toFixed(4)}
                          </ResponsiveTableCardRow>
                        </div>
                      </ResponsiveTableCard>
                    ))}
                  </ResponsiveTableCardList>
                )
              }
            />

            {/* Pagination */}
            <div className="flex items-center justify-between mt-3 pt-3 border-t text-sm">
              <span className="text-muted-foreground text-xs">
                {sorted.length === 0
                  ? "No sessions"
                  : `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, sorted.length)} of ${sorted.length}`}
              </span>
              <div className="flex gap-2">
                <HelpTip label={`Show the previous ${PAGE_SIZE} sessions`}>
                  <span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage((p) => Math.max(0, p - 1))}
                      disabled={page === 0}
                    >
                      Prev
                    </Button>
                  </span>
                </HelpTip>
                <HelpTip label={`Show the next ${PAGE_SIZE} sessions`}>
                  <span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        setPage((p) => Math.min(totalPages - 1, p + 1))
                      }
                      disabled={page >= totalPages - 1}
                    >
                      Next
                    </Button>
                  </span>
                </HelpTip>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
