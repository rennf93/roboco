"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { xApi } from "@/lib/api";
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
  key: "api_key" | "api_secret" | "access_token" | "access_token_secret";
  label: string;
}> = [
  { key: "api_key", label: "API key" },
  { key: "api_secret", label: "API key secret" },
  { key: "access_token", label: "Access token" },
  { key: "access_token_secret", label: "Access token secret" },
];

// The CEO's one-time (or rotate) entry of the 4 OAuth 1.0a user-context
// secrets from the X developer app. Write-only — the stored values are never
// displayed back, only whether they're set (mirrors the git-token card).
// Rendered chrome-less so it can nest inside the X-engine feature-flag row.
export function XCredentialsForm() {
  const queryClient = useQueryClient();
  const [values, setValues] = useState({
    api_key: "",
    api_secret: "",
    access_token: "",
    access_token_secret: "",
  });

  const { data: status, isLoading } = useQuery({
    queryKey: ["x", "credentials"],
    queryFn: () => xApi.getCredentialsStatus(),
  });

  const saveMutation = useMutation({
    mutationFn: () => xApi.setCredentials(values),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["x", "credentials"] });
      setValues({
        api_key: "",
        api_secret: "",
        access_token: "",
        access_token_secret: "",
      });
      toast.success("X credentials saved");
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
        The 4 OAuth 1.0a user-context secrets from your X developer app. Stored
        encrypted server-side; agents never see them and this panel never
        displays them again once saved.
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
              <Label htmlFor={`x-cred-${field.key}`}>
                {status?.has_credentials
                  ? `Replace ${field.label}`
                  : field.label}
              </Label>
            </HelpTip>
            <Input
              id={`x-cred-${field.key}`}
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
            <AlertDialogTitle>Clear X credentials?</AlertDialogTitle>
            <AlertDialogDescription>
              This will clear all stored X credentials. This cannot be undone.
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
