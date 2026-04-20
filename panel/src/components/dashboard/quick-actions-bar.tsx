"use client";

import { Button } from "@/components/ui/button";
import { CreateTaskDialog } from "@/components/tasks/create-task-dialog";
import { Users, Megaphone, BookOpen, Shield } from "lucide-react";
import Link from "next/link";

export function QuickActionsBar() {
  return (
    <div className="flex flex-wrap gap-3">
      <CreateTaskDialog />

      <Link href="/agents">
        <Button variant="outline">
          <Users className="h-4 w-4 mr-2" />
          Spawn Agent
        </Button>
      </Link>

      <Link href="/communications">
        <Button variant="outline">
          <Megaphone className="h-4 w-4 mr-2" />
          Broadcast Message
        </Button>
      </Link>

      <Link href="/journals">
        <Button variant="outline">
          <BookOpen className="h-4 w-4 mr-2" />
          View Journals
        </Button>
      </Link>

      <Link href="/auditor">
        <Button variant="outline">
          <Shield className="h-4 w-4 mr-2" />
          Auditor Report
        </Button>
      </Link>
    </div>
  );
}
