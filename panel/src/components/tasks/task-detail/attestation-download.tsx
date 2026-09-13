"use client";

import { Task } from "@/types";
import { useTaskAttestationDownload } from "@/hooks/use-tasks";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { toast } from "sonner";
import { Download, FileJson, FileText, Loader2 } from "lucide-react";

interface AttestationDownloadProps {
  task: Task;
}

export function AttestationDownload({ task }: AttestationDownloadProps) {
  const download = useTaskAttestationDownload(task.id);

  // No verification stamp yet — self_verified is the dev's stamp and
  // qa_verified the QA's — so there is nothing to attest and the endpoint
  // would only emit a blank receipt. The action stays visible but disabled
  // with an explanation instead of silently disappearing.
  const attested = task.self_verified || task.qa_verified === true;

  const handleDownload = async (format: "md" | "json") => {
    try {
      await download.mutateAsync({ format });
      toast.success(
        format === "md"
          ? "Verification receipt (.md) downloaded"
          : "Verification receipt (.json) downloaded",
      );
    } catch (error) {
      toast.error(
        `Failed to download receipt: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  if (!attested) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="outline"
            size="icon"
            disabled
            aria-label="Download attestation"
          >
            <Download className="h-4 w-4" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          No verification receipt yet — available once the work passes
          verification.
        </TooltipContent>
      </Tooltip>
    );
  }

  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              disabled={download.isPending}
              aria-label="Download attestation"
            >
              {download.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Download className="h-4 w-4" />
              )}
            </Button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent>
          Download the per-task verification receipt
        </TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => handleDownload("md")}>
          <FileText className="h-4 w-4 mr-2" />
          Receipt (.md) — human-readable
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handleDownload("json")}>
          <FileJson className="h-4 w-4 mr-2" />
          Receipt (.json) — full attestation
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
