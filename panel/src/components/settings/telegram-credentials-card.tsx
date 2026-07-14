"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { telegramApi } from "@/lib/api";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
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

const FIELDS: Array<{ key: "bot_token" | "chat_id"; label: string }> = [
  { key: "bot_token", label: "Bot token (from @BotFather)" },
  { key: "chat_id", label: "Chat id (destination)" },
];

// The CEO's one-time entry of the Telegram bot token + destination chat id.
// Write-only — the stored values are never displayed back, only whether
// they're set (mirrors the X / git-token cards). Both are required together:
// a token alone can't target a DM. Rendered chrome-less so it can nest inside
// the Telegram feature-flag row.
export function TelegramCredentialsForm() {
  const queryClient = useQueryClient();
  const [values, setValues] = useState({ bot_token: "", chat_id: "" });

  const { data: status, isLoading } = useQuery({
    queryKey: ["telegram", "credentials"],
    queryFn: () => telegramApi.getCredentialsStatus(),
  });

  const saveMutation = useMutation({
    mutationFn: () => telegramApi.setCredentials(values),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["telegram", "credentials"] });
      setValues({ bot_token: "", chat_id: "" });
      toast.success("Telegram credentials saved");
    },
    onError: (error) => {
      toast.error(
        `Failed to save: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  const allFilled = FIELDS.every((f) => values[f.key].trim().length > 0);
  const noneFilled = FIELDS.every((f) => values[f.key].trim().length === 0);
  const canSave = allFilled || (noneFilled && !!status?.has_credentials);
  const isClearing = noneFilled && !!status?.has_credentials;
  const [confirmClear, setConfirmClear] = useState(false);

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        The bot token from <span className="font-medium">@BotFather</span> and the
        chat id to DM (your user/channel id). Stored encrypted server-side; agents
        never see them and this panel never displays them again once saved.
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
            <Label htmlFor={`tg-cred-${field.key}`}>
              {status?.has_credentials ? `Replace ${field.label}` : field.label}
            </Label>
            <Input
              id={`tg-cred-${field.key}`}
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
        Set both to save (or rotate); leave both blank and save to clear.
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
            <AlertDialogTitle>Clear Telegram credentials?</AlertDialogTitle>
            <AlertDialogDescription>
              This will clear the stored bot token and chat id. This cannot be
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