"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { githubAppApi } from "@/lib/api";
import type { GitHubAppInstallationRepository } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { HelpTip } from "@/components/ui/help-tip";
import { FolderGit2, Lock, Search } from "lucide-react";

export interface SelectedRepo {
  git_url: string;
  installation_id: number;
}

interface SelectRepoDialogProps {
  onSelect: (repo: SelectedRepo) => void;
}

// "Select repo" button + picker for the New Project dialog: lists the repos
// a configured GitHub App installation can access and, on pick, hands back
// the clone URL + installation id (the caller fills git_url and stashes the
// installation id into the create payload). Disabled — with a HelpTip
// explaining why — until the App is configured on the Settings page; manual
// Git URL + PAT entry stays the default, unaffected fallback either way.
export function SelectRepoDialog({ onSelect }: SelectRepoDialogProps) {
  const [open, setOpen] = useState(false);
  const [installationId, setInstallationId] = useState<number | null>(null);
  const [search, setSearch] = useState("");

  const { data: credStatus } = useQuery({
    queryKey: ["github-app", "credentials"],
    queryFn: () => githubAppApi.getCredentialsStatus(),
  });
  const configured = !!credStatus?.has_credentials;

  const { data: installations = [], isLoading: loadingInstallations } =
    useQuery({
      queryKey: ["github-app", "installations"],
      queryFn: () => githubAppApi.listInstallations(),
      enabled: open && configured,
    });

  // The sole installation applies automatically; a picker only appears when
  // there's a real choice. Derived (not synced via effect) so there's no
  // set-state-in-effect to get wrong.
  const effectiveInstallationId =
    installations.length === 1 ? installations[0].id : installationId;

  const { data: repos = [], isLoading: loadingRepos } = useQuery({
    queryKey: [
      "github-app",
      "installations",
      effectiveInstallationId,
      "repositories",
    ],
    queryFn: () =>
      githubAppApi.listInstallationRepositories(
        effectiveInstallationId as number,
      ),
    enabled: open && configured && effectiveInstallationId !== null,
  });

  const filteredRepos = repos.filter((r) =>
    r.full_name.toLowerCase().includes(search.trim().toLowerCase()),
  );

  const reset = () => {
    setInstallationId(null);
    setSearch("");
  };

  const handlePick = (repo: GitHubAppInstallationRepository) => {
    if (effectiveInstallationId === null) return;
    onSelect({
      git_url: repo.clone_url,
      installation_id: effectiveInstallationId,
    });
    setOpen(false);
    reset();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <HelpTip
        label={
          configured
            ? "Browse repos the GitHub App installation can access — picking one fills the Git URL and binds this project to that installation for token auth."
            : "Configure the GitHub App (Settings page) first to browse installation repos here."
        }
      >
        <span>
          <Button
            type="button"
            variant="outline"
            disabled={!configured}
            onClick={() => setOpen(true)}
          >
            <FolderGit2 className="mr-2 h-4 w-4" />
            Select repo
          </Button>
        </span>
      </HelpTip>

      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>Select a repository</DialogTitle>
          <DialogDescription>
            Only repositories the GitHub App installation can access are listed.
          </DialogDescription>
        </DialogHeader>

        {installations.length > 1 && (
          <div className="grid gap-2">
            <HelpTip label="The App can be installed on more than one account/org — pick which one to browse.">
              <span className="text-sm font-medium">Installation</span>
            </HelpTip>
            <Select
              value={installationId !== null ? String(installationId) : ""}
              onValueChange={(v) => setInstallationId(Number(v))}
            >
              <SelectTrigger>
                <SelectValue placeholder="Choose an installation" />
              </SelectTrigger>
              <SelectContent>
                {installations.map((inst) => (
                  <SelectItem key={inst.id} value={String(inst.id)}>
                    {inst.account_login}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        {effectiveInstallationId !== null && (
          <div className="grid gap-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search repositories..."
                className="pl-8"
              />
            </div>
            <ScrollArea className="h-64 rounded-md border">
              <div className="p-1">
                {loadingRepos && (
                  <p className="p-3 text-sm text-muted-foreground">
                    Loading...
                  </p>
                )}
                {!loadingRepos && filteredRepos.length === 0 && (
                  <p className="p-3 text-sm text-muted-foreground">
                    No matching repositories
                  </p>
                )}
                {filteredRepos.map((repo) => (
                  <button
                    key={repo.full_name}
                    type="button"
                    onClick={() => handlePick(repo)}
                    className="flex w-full items-center justify-between gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent"
                  >
                    <span className="truncate">{repo.full_name}</span>
                    {repo.private && (
                      <HelpTip label="Private repository">
                        <Lock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      </HelpTip>
                    )}
                  </button>
                ))}
              </div>
            </ScrollArea>
          </div>
        )}

        {open &&
          configured &&
          !loadingInstallations &&
          installations.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No installations found for the configured App.
            </p>
          )}
      </DialogContent>
    </Dialog>
  );
}
