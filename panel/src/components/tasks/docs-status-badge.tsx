import { Badge } from "@/components/ui/badge";
import { FileCheck, GitPullRequest, Check, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { HelpTip } from "@/components/ui/help-tip";

// The compact badges below abbreviate to a single word ("Docs"/"Pending",
// "PR"/"Pending") that reads ambiguously out of context — these spell out
// what each state actually means.
const DOCS_DESCRIPTIONS = {
  complete: "Documentation for this task is complete.",
  pending: "Documentation has not been written yet.",
};
const PR_DESCRIPTIONS = {
  created: "A pull request has been opened for this task.",
  pending: "No pull request has been opened yet.",
};

interface DocsStatusBadgeProps {
  docsComplete?: boolean;
  prCreated?: boolean;
  variant?: "compact" | "full";
  className?: string;
}

export function DocsStatusBadge({
  docsComplete,
  prCreated,
  variant = "compact",
  className = "",
}: DocsStatusBadgeProps) {
  // If both undefined, show nothing
  if (docsComplete === undefined && prCreated === undefined) {
    return null;
  }

  if (variant === "compact") {
    return (
      <div className={cn("flex items-center gap-1", className)}>
        {docsComplete !== undefined && (
          <HelpTip
            label={
              docsComplete ? DOCS_DESCRIPTIONS.complete : DOCS_DESCRIPTIONS.pending
            }
          >
            <Badge
              variant="outline"
              className={cn(
                "text-xs",
                docsComplete
                  ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
                  : "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300",
              )}
            >
              <FileCheck className="h-3 w-3 mr-1" />
              {docsComplete ? "Docs" : "Pending"}
            </Badge>
          </HelpTip>
        )}
        {prCreated !== undefined && (
          <HelpTip
            label={prCreated ? PR_DESCRIPTIONS.created : PR_DESCRIPTIONS.pending}
          >
            <Badge
              variant="outline"
              className={cn(
                "text-xs",
                prCreated
                  ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
                  : "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300",
              )}
            >
              <GitPullRequest className="h-3 w-3 mr-1" />
              {prCreated ? "PR" : "Pending"}
            </Badge>
          </HelpTip>
        )}
      </div>
    );
  }

  // Full variant with labels
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <HelpTip
        label={
          docsComplete
            ? DOCS_DESCRIPTIONS.complete
            : "Blocks the awaiting_documentation → awaiting_pm_review handoff until the documenter marks it done."
        }
      >
        <div className="flex items-center justify-between text-sm">
          <span className="flex items-center gap-1 text-muted-foreground">
            <FileCheck className="h-4 w-4" />
            Documentation
          </span>
          <span className="flex items-center gap-1">
            {docsComplete ? (
              <Check className="h-4 w-4 text-green-600" />
            ) : (
              <X className="h-4 w-4 text-amber-600" />
            )}
            {docsComplete ? "Complete" : "Pending"}
          </span>
        </div>
      </HelpTip>
      <HelpTip
        label={
          prCreated
            ? PR_DESCRIPTIONS.created
            : "The PR is opened before QA review, not after — this task hasn't reached that step yet."
        }
      >
        <div className="flex items-center justify-between text-sm">
          <span className="flex items-center gap-1 text-muted-foreground">
            <GitPullRequest className="h-4 w-4" />
            Pull Request
          </span>
          <span className="flex items-center gap-1">
            {prCreated ? (
              <Check className="h-4 w-4 text-green-600" />
            ) : (
              <X className="h-4 w-4 text-amber-600" />
            )}
            {prCreated ? "Created" : "Pending"}
          </span>
        </div>
      </HelpTip>
    </div>
  );
}
