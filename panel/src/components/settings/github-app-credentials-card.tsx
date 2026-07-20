"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { githubAppApi } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
import { FolderGit2, Key, KeyRound, Save, Trash2 } from "lucide-react";
import { toast } from "sonner";

// The CEO's one-time entry of a GitHub App's id + RSA private key (PEM,
// downloaded once from the App's settings page on github.com). Write-only —
// the key is never displayed back, only whether it's set (mirrors the
// Telegram/X credentials cards). Once set, projects can bind to one of the
// App's installations (New Project dialog's "Select repo" picker) and git
// operations mint short-lived installation tokens instead of a stored PAT.
export function GitHubAppCredentialsCard() {
  const queryClient = useQueryClient();
  const [appId, setAppId] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);

  const { data: status, isLoading } = useQuery({
    queryKey: ["github-app", "credentials"],
    queryFn: () => githubAppApi.getCredentialsStatus(),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["github-app", "credentials"] });

  const saveMutation = useMutation({
    mutationFn: () =>
      githubAppApi.setCredentials({ app_id: appId, private_key: privateKey }),
    onSuccess: () => {
      invalidate();
      setAppId("");
      setPrivateKey("");
      toast.success("GitHub App credentials saved");
    },
    onError: (error) => {
      toast.error(
        `Failed to save: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  const clearMutation = useMutation({
    mutationFn: () => githubAppApi.clearCredentials(),
    onSuccess: () => {
      invalidate();
      toast.success("GitHub App credentials cleared");
    },
    onError: (error) => {
      toast.error(
        `Failed to clear: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  const canSave = appId.trim().length > 0 && privateKey.trim().length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FolderGit2 className="h-5 w-5" />
          GitHub App
        </CardTitle>
        <CardDescription>
          The App id + private key from a GitHub App you created on github.com.
          Once set, a project can bind to one of the App&apos;s installations
          and git operations use a short-lived installation token instead of a
          personal access token.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2 rounded-md border p-3">
          {status?.has_credentials ? (
            <HelpTip label="App id + private key are both stored — projects can bind to an installation via the New Project dialog's Select repo button.">
              <Key className="h-4 w-4 text-green-500" />
            </HelpTip>
          ) : (
            <HelpTip label="Missing — the Select repo picker stays disabled and every project falls back to its own PAT until both fields below are set.">
              <KeyRound className="h-4 w-4 text-amber-500" />
            </HelpTip>
          )}
          {status?.has_credentials ? (
            <span className="text-sm text-green-600 dark:text-green-400">
              Credentials are set
            </span>
          ) : (
            <span className="text-sm text-amber-600 dark:text-amber-400">
              {isLoading ? "Checking..." : "No credentials configured"}
            </span>
          )}
        </div>

        <div className="space-y-2">
          <HelpTip label="The numeric App id shown on the App's github.com settings page (General tab). Not a secret, but stored alongside the key.">
            <Label htmlFor="github-app-id">
              {status?.has_credentials ? "Replace App id" : "App id"}
            </Label>
          </HelpTip>
          <Input
            id="github-app-id"
            value={appId}
            onChange={(e) => setAppId(e.target.value)}
            placeholder="123456"
            inputMode="numeric"
          />
        </div>

        <div className="space-y-2">
          <HelpTip label="Paste the full .pem contents downloaded once from the App's settings page. Stored encrypted server-side; never displayed again once saved.">
            <Label htmlFor="github-app-private-key">
              {status?.has_credentials
                ? "Replace private key"
                : "Private key (PEM)"}
            </Label>
          </HelpTip>
          <Textarea
            id="github-app-private-key"
            value={privateKey}
            onChange={(e) => setPrivateKey(e.target.value)}
            placeholder="-----BEGIN RSA PRIVATE KEY-----&#10;...&#10;-----END RSA PRIVATE KEY-----"
            className="min-h-32 font-mono text-xs"
          />
        </div>

        <p className="text-xs text-muted-foreground">
          Both fields are required together — set both to save (or rotate).
        </p>

        <div className="flex gap-2">
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending || !canSave}
          >
            <Save className="mr-2 h-4 w-4" />
            {saveMutation.isPending ? "Saving..." : "Save"}
          </Button>
          {status?.has_credentials && (
            <Button
              variant="outline"
              onClick={() => setConfirmClear(true)}
              disabled={clearMutation.isPending}
            >
              <Trash2 className="mr-2 h-4 w-4" />
              Clear
            </Button>
          )}
        </div>
      </CardContent>

      <AlertDialog
        open={confirmClear}
        onOpenChange={(open) => {
          if (!open) setConfirmClear(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear GitHub App credentials?</AlertDialogTitle>
            <AlertDialogDescription>
              This clears the stored App id and private key. Projects already
              bound to an installation fall back to their own PAT (if set) until
              you configure the App again. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmClear(false);
                clearMutation.mutate();
              }}
            >
              Clear
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
