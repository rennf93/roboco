"use client";

import { Button } from "@/components/ui/button";
import { CreateTaskDialog } from "@/components/tasks/create-task-dialog";
import { HelpTip } from "@/components/ui/help-tip";
import { Users, BookOpen, Shield, Sparkles, Bot } from "lucide-react";
import Link from "next/link";

export function QuickActionsBar() {
  return (
    <div className="flex flex-wrap gap-3">
      <CreateTaskDialog />

      <Link href="/agents" prefetch={false}>
        <HelpTip label="Agents page — view the roster and spawn a new agent run">
          <Button variant="outline">
            <Users className="h-4 w-4 mr-2" />
            Spawn Agent
          </Button>
        </HelpTip>
      </Link>

      <Link href="/prompter" prefetch={false}>
        <HelpTip label="Chat-based interview to draft and submit a new task">
          <Button variant="outline">
            <Sparkles className="h-4 w-4 mr-2" />
            Task Intake
          </Button>
        </HelpTip>
      </Link>

      <Link href="/business?tab=secretary" prefetch={false}>
        <HelpTip label="Chat with the Secretary — company state and gated CEO directives">
          <Button variant="outline">
            <Bot className="h-4 w-4 mr-2" />
            Secretary
          </Button>
        </HelpTip>
      </Link>

      <Link href="/journals" prefetch={false}>
        <HelpTip label="Browse agent journal entries and learnings">
          <Button variant="outline">
            <BookOpen className="h-4 w-4 mr-2" />
            View Journals
          </Button>
        </HelpTip>
      </Link>

      <Link href="/auditor" prefetch={false}>
        <HelpTip label="The Auditor's flagged issues and reports">
          <Button variant="outline">
            <Shield className="h-4 w-4 mr-2" />
            Auditor Report
          </Button>
        </HelpTip>
      </Link>
    </div>
  );
}
