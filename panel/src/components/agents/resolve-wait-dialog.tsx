"use client";

import { useState } from "react";
import { useResolveWait } from "@/hooks/use-agents";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogDescription,
} from "@/components/ui/dialog";
import { Send } from "lucide-react";
import { toast } from "sonner";

interface ResolveWaitDialogProps {
  agentId: string;
}

export function ResolveWaitDialog({ agentId }: ResolveWaitDialogProps) {
  const [open, setOpen] = useState(false);
  const [resolution, setResolution] = useState("");
  const resolveWait = useResolveWait();

  const handleResolve = async () => {
    if (!resolution.trim()) {
      toast.error("Please provide a resolution");
      return;
    }

    try {
      await resolveWait.mutateAsync({ agentId, resolution });
      toast.success("Resolution sent to agent");
      setOpen(false);
      setResolution("");
    } catch {
      toast.error("Failed to resolve wait");
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Send className="h-4 w-4 mr-2" />
          Resolve Wait
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Resolve Agent Wait</DialogTitle>
          <DialogDescription>
            Provide context or instructions to help the agent continue.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="resolution">Resolution Message</Label>
            <Textarea
              id="resolution"
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
              placeholder="Provide the information or decision the agent needs to continue..."
              rows={4}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleResolve} disabled={resolveWait.isPending}>
              {resolveWait.isPending ? "Sending..." : "Send Resolution"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
