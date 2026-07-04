"use client";

import { Button } from "@/components/ui/button";
import { CreateTaskDialog } from "@/components/tasks/create-task-dialog";
import { Users, BookOpen, Shield, Sparkles, Bot } from "lucide-react";
import Link from "next/link";

export function QuickActionsBar() {
  return (
    <div className="flex flex-wrap gap-3">
      <CreateTaskDialog />

      <Link href="/agents" prefetch={false}>
        <Button variant="outline">
          <Users className="h-4 w-4 mr-2" />
          Spawn Agent
        </Button>
      </Link>

      <Link href="/prompter" prefetch={false}>
        <Button variant="outline">
          <Sparkles className="h-4 w-4 mr-2" />
          Task Intake
        </Button>
      </Link>

      <Link href="/business?tab=secretary" prefetch={false}>
        <Button variant="outline">
          <Bot className="h-4 w-4 mr-2" />
          Secretary
        </Button>
      </Link>

      <Link href="/journals" prefetch={false}>
        <Button variant="outline">
          <BookOpen className="h-4 w-4 mr-2" />
          View Journals
        </Button>
      </Link>

      <Link href="/auditor" prefetch={false}>
        <Button variant="outline">
          <Shield className="h-4 w-4 mr-2" />
          Auditor Report
        </Button>
      </Link>
    </div>
  );
}
