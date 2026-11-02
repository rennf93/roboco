"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { entireApi } from "@/lib/api";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Key, KeyRound, RotateCcw, Save, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

const STATUS_KEY = ["entire", "trails", "status"];
const TOKEN_KEY = ["entire", "credentials"];

// The CEO's Entire token for the trails bridge, and the bridge's queue.
// Write-only like the other credential forms: the token is never displayed
// back, only whether it is set. Rendered chrome-less so it can nest inside
// the Entire trails feature-flag row. Projects opt in on their own settings
// page (Budget & ops card).
export function EntireTrailsForm() {
  const queryClient = useQueryClient();
  const [token, setToken] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);

  const { data: tokenStatus, isLoading } = useQuery({
    queryKey: TOKEN_KEY,
    queryFn: () => entireApi.getTokenStatus(),
  });
  const { data: status } = useQuery({
    queryKey: STATUS_KEY,
    queryFn: () => entireApi.getTrailsStatus(),
    refetchInterval: 30_000,
  });

  const saveMutation = useMutation({
    mutationFn: () => entireApi.setToken(token.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: TOKEN_KEY });
      queryClient.invalidateQueries({ queryKey: STATUS_KEY });
      setToken("");
      toast.success("Entire token saved");
    },
    onError: (error) => {
      toast.error(
        `Failed to save: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });
  const retryMutation = useMutation({
    mutationFn: () => entireApi.retryTrails(),
    onSuccess: ({ retried }) => {
      queryClient.invalidateQueries({ queryKey: STATUS_KEY });
      toast.success(`${retried} stopped operation(s) queued again`);
    },
    onError: (error) => {
      toast.error(
        `Failed to retry: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  const hasToken = !!tokenStatus?.has_token;
  const isClearing = token.trim().length === 0 && hasToken;

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        An Entire API token for the account that owns the trails. Stored
        encrypted server-side and passed only to the dispatcher&apos;s Entire
        CLI; agents never see it and this panel never displays it again once
        saved.
      </p>

      <div className="flex items-center gap-2 rounded-md border p-3">
        {hasToken ? (
          <HelpTip label="A token is stored — the dispatcher can reach Entire.">
            <Key className="h-4 w-4 text-green-500" />
          </HelpTip>
        ) : (
          <HelpTip label="Missing — nothing reaches Entire; queued trail updates wait until a token is set.">
            <KeyRound className="h-4 w-4 text-amber-500" />
          </HelpTip>
        )}
        {hasToken ? (
          <span className="text-sm text-green-600 dark:text-green-400">
            Token is set
          </span>
        ) : (
          <span className="text-sm text-amber-600 dark:text-amber-400">
            {isLoading ? "Checking..." : "No token configured"}
          </span>
        )}
      </div>

      <div className="space-y-2">
        <HelpTip label="Stored encrypted server-side; never displayed again once saved.">
          <Label htmlFor="entire-token">
            {hasToken ? "Replace token" : "Token"}
          </Label>
        </HelpTip>
        <Input
          id="entire-token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="••••••••••••"
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Leave blank and save to clear the stored token.
      </p>
      <Button
        onClick={() =>
          isClearing ? setConfirmClear(true) : saveMutation.mutate()
        }
        disabled={
          saveMutation.isPending || (token.trim().length === 0 && !hasToken)
        }
      >
        <Save className="mr-2 h-4 w-4" />
        {saveMutation.isPending ? "Saving..." : "Save"}
      </Button>

      {status && (
        <div className="space-y-2 rounded-md border p-3 text-sm">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground">
            <span>{status.open_trails} open trail(s)</span>
            <span>{status.pending} queued</span>
            <span
              className={
                status.dead > 0 ? "text-amber-600 dark:text-amber-400" : ""
              }
            >
              {status.dead} stopped
            </span>
            {!status.cli_available && (
              <span className="text-amber-600 dark:text-amber-400">
                Entire CLI not installed in this image
              </span>
            )}
          </div>
          {status.dead > 0 && (
            <div className="space-y-2">
              {status.last_error && (
                <p className="flex items-start gap-2 text-amber-600 dark:text-amber-400">
                  <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                  <span className="break-all">{status.last_error}</span>
                </p>
              )}
              <HelpTip label="Puts every stopped operation back in its tree's queue with a fresh attempt budget. Fix the cause first.">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => retryMutation.mutate()}
                  disabled={retryMutation.isPending}
                >
                  <RotateCcw className="mr-2 h-4 w-4" />
                  Retry stopped operations
                </Button>
              </HelpTip>
            </div>
          )}
        </div>
      )}

      <AlertDialog
        open={confirmClear}
        onOpenChange={(open) => {
          if (!open) setConfirmClear(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear the Entire token?</AlertDialogTitle>
            <AlertDialogDescription>
              Trail updates stop reaching Entire and wait in the queue until a
              token is set again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmClear(false);
                saveMutation.mutate();
              }}
            >
              Clear
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
