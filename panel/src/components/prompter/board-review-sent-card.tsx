"use client";

import Link from "next/link";
import { Users, ExternalLink, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";

interface BoardReviewSentCardProps {
  /** The umbrella task id — the single board-review / CEO-approve unit. */
  taskId: string;
  taskTitle: string;
  rootSubtaskCount: number;
  waveCount: number;
  onStartAnother: () => void;
}

/**
 * The MegaTask "Board review & Start" confirmation: the umbrella + root-subtasks
 * were created HELD (umbrella assigned to the Product Owner, root-subtasks in
 * BACKLOG) for the Product Owner + Head of Marketing to review. Nothing is
 * dispatched yet — the CEO releases the sequenced tasks with Approve & Start on
 * the umbrella task once the board finishes. This is the batch analogue of the
 * single-draft board route's "sent to the board" wait, reusing the existing CEO
 * Approve & Start gate on the umbrella (which fires ``approve_and_start`` →
 * ``_activate_batch_root_subtasks``).
 */
export function BoardReviewSentCard({
  taskId,
  taskTitle,
  rootSubtaskCount,
  waveCount,
  onStartAnother,
}: BoardReviewSentCardProps) {
  return (
    <Card className="border-primary/30 bg-primary/5">
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <Users className="h-5 w-5 text-primary" />
          <CardTitle className="text-sm font-semibold text-primary">
            Sent to the Board for review
          </CardTitle>
        </div>
      </CardHeader>

      <CardContent className="pb-3 space-y-2">
        <p className="text-sm font-medium">{taskTitle}</p>
        <div className="flex items-center gap-2">
          <HelpTip label="Each is a real root-subtask with its own project, branch, and PR, coordinated by the Main PM.">
            <Badge variant="secondary" className="text-xs">
              {rootSubtaskCount} task{rootSubtaskCount === 1 ? "" : "s"}
            </Badge>
          </HelpTip>
          <HelpTip label="Tasks in the same wave don't overlap and run in parallel; later waves wait for their dependencies to land first.">
            <Badge variant="outline" className="text-xs">
              {waveCount} wave{waveCount === 1 ? "" : "s"}
            </Badge>
          </HelpTip>
          <HelpTip label={`Full umbrella task ID: ${taskId}`}>
            <span className="text-xs text-muted-foreground">
              ID: {taskId.slice(0, 8)}…
            </span>
          </HelpTip>
        </div>
        <p className="text-xs text-muted-foreground">
          The Product Owner and Head of Marketing are reviewing this MegaTask.
          Nothing is dispatched yet. Once they finish, open the umbrella task
          and use <span className="font-medium">Approve &amp; Start</span> to
          release the sequenced tasks. You can leave and come back.
        </p>
      </CardContent>

      <CardFooter className="gap-2 pt-0">
        <HelpTip label="Opens the umbrella task in a new tab — use Approve & Start there once the board finishes">
          <Button variant="outline" size="sm" asChild className="flex-1">
            <Link
              prefetch={false}
              href={`/tasks/${taskId}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
              View umbrella task
            </Link>
          </Button>
        </HelpTip>
        <HelpTip label="Ends this session and resets to a fresh intake chat">
          <Button size="sm" className="flex-1" onClick={onStartAnother}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            Start another
          </Button>
        </HelpTip>
      </CardFooter>
    </Card>
  );
}
