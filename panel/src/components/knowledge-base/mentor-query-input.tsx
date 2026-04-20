"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Send, Loader2, Brain, User, BookMarked, MessageCircle } from "lucide-react";

interface MentorQueryInputProps {
  onSubmit: (question: string) => void;
  isLoading: boolean;
}

export function MentorQueryInput({ onSubmit, isLoading }: MentorQueryInputProps) {
  const [question, setQuestion] = useState("");

  const handleSubmit = () => {
    if (question.trim() && !isLoading) {
      onSubmit(question.trim());
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <Card className="border-primary/30 bg-primary/5">
      <CardContent className="pt-4 space-y-4">
        {/* Header - Clearly different from Ask AI */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-full bg-primary/20">
              <Brain className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h3 className="font-semibold text-sm">Personal Mentor</h3>
              <p className="text-xs text-muted-foreground">
                Tailored to your role & experience
              </p>
            </div>
          </div>
          <div className="flex gap-1.5">
            <Badge variant="outline" className="text-xs gap-1 bg-background">
              <User className="h-3 w-3" />
              Role-aware
            </Badge>
            <Badge variant="outline" className="text-xs gap-1 bg-background">
              <BookMarked className="h-3 w-3" />
              Journals
            </Badge>
            <Badge variant="outline" className="text-xs gap-1 bg-background">
              <MessageCircle className="h-3 w-3" />
              Follow-ups
            </Badge>
          </div>
        </div>

        {/* Input */}
        <div className="relative">
          <Textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask your mentor about standards, workflows, or get guidance..."
            className="min-h-24 pr-12 resize-none bg-background"
            disabled={isLoading}
          />
          <Button
            size="icon"
            className="absolute bottom-2 right-2"
            onClick={handleSubmit}
            disabled={!question.trim() || isLoading}
          >
            {isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          Press Enter to send, Shift+Enter for new line
        </p>
      </CardContent>
    </Card>
  );
}
