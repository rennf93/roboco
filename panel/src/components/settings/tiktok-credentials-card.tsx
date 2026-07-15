"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { videoApi } from "@/lib/api";
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
import { Key, KeyRound, Save } from "lucide-react";
import { toast } from "sonner";

const FIELDS: Array<{
  key: "client_key" | "client_secret" | "access_token" | "refresh_token";
  label: string;
}> = [
  { key: "client_key", label: "Client key" },
  { key: "client_secret", label: "Client secret" },
  { key: "access_token", label: "Access token" },
  { key: "refresh_token", label: "Refresh token" },
];

// The CEO's one-time (or rotate) entry of the 4 OAuth2 secrets from the
// TikTok developer app. Write-only — the stored values are never displayed
// back, only whether they're set (mirrors x-credentials-card.tsx). Rendered
// chrome-less so it can nest inside the video-engine feature-flag row.
export function TikTokCredentialsForm() {
  const queryClient = useQueryClient();
  const [values, setValues] = useState({
    client_key: "",
    client_secret: "",
    access_token: "",
    refresh_token: "",
  });

  const { data: status, isLoading } = useQuery({
    queryKey: ["video", "tiktok-credentials"],
    queryFn: () => videoApi.getCredentialsStatus(),
  });

  const saveMutation = useMutation({
    mutationFn: () => videoApi.setCredentials(values),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["video", "tiktok-credentials"],
      });
      setValues({
        client_key: "",
        client_secret: "",
        access_token: "",
        refresh_token: "",
      });
      toast.success("TikTok credentials saved");
    },
    onError: (error) => {
      toast.error(
        `Failed to save: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  const allFilled = FIELDS.every((f) => values[f.key].trim().length > 0);
  const noneFilled = FIELDS.every((f) => values[f.key].trim().length === 0);
  // A genuine save is either "set all 4" or, when something is already
  // stored, "clear all 4". All-empty with nothing stored is a true no-op.
  const canSave = allFilled || (noneFilled && !!status?.has_credentials);
  // Clearing stored secrets is destructive and irreversible — confirm it.
  const isClearing = noneFilled && !!status?.has_credentials;
  const [confirmClear, setConfirmClear] = useState(false);

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        The 4 OAuth2 secrets from your TikTok developer app. Stored encrypted
        server-side; agents never see them and this panel never displays them
        again once saved.
      </p>

      <div className="flex items-center gap-2 rounded-md border p-3">
        {status?.has_credentials ? (
          <>
            <Key className="h-4 w-4 text-green-500" />
            <span className="text-sm text-green-600 dark:text-green-400">
              Credentials are set
            </span>
          </>
        ) : (
          <>
            <KeyRound className="h-4 w-4 text-amber-500" />
            <span className="text-sm text-amber-600 dark:text-amber-400">
              {isLoading ? "Checking..." : "No credentials configured"}
            </span>
          </>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {FIELDS.map((field) => (
          <div key={field.key} className="space-y-2">
            <HelpTip label="Stored encrypted server-side; never displayed again once saved.">
              <Label htmlFor={`tiktok-cred-${field.key}`}>
                {status?.has_credentials
                  ? `Replace ${field.label}`
                  : field.label}
              </Label>
            </HelpTip>
            <Input
              id={`tiktok-cred-${field.key}`}
              type="password"
              value={values[field.key]}
              onChange={(e) =>
                setValues((prev) => ({ ...prev, [field.key]: e.target.value }))
              }
              placeholder="••••••••••••"
            />
          </div>
        ))}
      </div>

      <p className="text-xs text-muted-foreground">
        Set all 4 to save (or rotate); leave all 4 blank and save to clear.
      </p>

      <Button
        onClick={() =>
          isClearing ? setConfirmClear(true) : saveMutation.mutate()
        }
        disabled={saveMutation.isPending || !canSave}
      >
        <Save className="mr-2 h-4 w-4" />
        {saveMutation.isPending ? "Saving..." : "Save"}
      </Button>

      <AlertDialog
        open={confirmClear}
        onOpenChange={(open) => {
          if (!open) setConfirmClear(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear TikTok credentials?</AlertDialogTitle>
            <AlertDialogDescription>
              This will clear all stored TikTok credentials. This cannot be
              undone.
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
