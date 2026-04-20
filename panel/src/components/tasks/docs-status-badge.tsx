import { Badge } from "@/components/ui/badge";
import { FileCheck, GitPullRequest, Check, X } from "lucide-react";
import { cn } from "@/lib/utils";

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
          <Badge
            variant="outline"
            className={cn(
              "text-xs",
              docsComplete
                ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
                : "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300"
            )}
          >
            <FileCheck className="h-3 w-3 mr-1" />
            {docsComplete ? "Docs" : "Pending"}
          </Badge>
        )}
        {prCreated !== undefined && (
          <Badge
            variant="outline"
            className={cn(
              "text-xs",
              prCreated
                ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
                : "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300"
            )}
          >
            <GitPullRequest className="h-3 w-3 mr-1" />
            {prCreated ? "PR" : "Pending"}
          </Badge>
        )}
      </div>
    );
  }

  // Full variant with labels
  return (
    <div className={cn("flex flex-col gap-2", className)}>
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
    </div>
  );
}
