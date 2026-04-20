"use client";

import { useState, useRef, useEffect } from "react";
import { Task, SubTask, TaskPlan } from "@/types";
import { useUpdateTask } from "@/hooks/use-tasks";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Markdown } from "@/components/ui/markdown";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  FileText,
  ListChecks,
  AlertTriangle,
  HelpCircle,
  Lightbulb,
  Edit3,
  Eye,
  Check,
  X,
  Plus,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

interface TabPlanProps {
  task: Task;
}

// Risk severity colors
const severityColors: Record<string, string> = {
  low: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
  medium: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
  high: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
};

// Generate a simple unique ID
function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

// ============================================================================
// Approach Section (Editable Markdown)
// ============================================================================
function ApproachSection({ task, plan }: { task: Task; plan: TaskPlan }) {
  const updateTask = useUpdateTask();
  const [isEditing, setIsEditing] = useState(false);
  const [localEditValue, setLocalEditValue] = useState("");
  const [editMode, setEditMode] = useState<"write" | "preview">("write");

  // Display prop value when not editing, local value when editing
  const editValue = isEditing ? localEditValue : plan.approach;
  const setEditValue = (value: string) => setLocalEditValue(value);

  // Start editing - copy current prop value to local state
  const startEditing = () => {
    setLocalEditValue(plan.approach);
    setIsEditing(true);
  };

  const handleSave = async () => {
    if (editValue === plan.approach) {
      setIsEditing(false);
      return;
    }

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { plan: { ...plan, approach: editValue } },
      });
      setIsEditing(false);
      setEditMode("write");
    } catch {
      toast.error("Failed to update approach");
    }
  };

  const handleCancel = () => {
    setEditValue(plan.approach);
    setIsEditing(false);
    setEditMode("write");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") handleCancel();
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSave();
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <FileText className="h-5 w-5" />
            Approach
          </CardTitle>
          {isEditing ? (
            <div className="flex items-center gap-2">
              <Tabs value={editMode} onValueChange={(v) => setEditMode(v as "write" | "preview")}>
                <TabsList className="h-8">
                  <TabsTrigger value="write" className="text-xs px-2 h-6">
                    <Edit3 className="h-3 w-3 mr-1" />Write
                  </TabsTrigger>
                  <TabsTrigger value="preview" className="text-xs px-2 h-6">
                    <Eye className="h-3 w-3 mr-1" />Preview
                  </TabsTrigger>
                </TabsList>
              </Tabs>
              <Button size="sm" variant="ghost" onClick={handleCancel}><X className="h-4 w-4" /></Button>
              <Button size="sm" onClick={handleSave} disabled={updateTask.isPending}>
                <Check className="h-4 w-4 mr-1" />Save
              </Button>
            </div>
          ) : (
            <Button size="sm" variant="ghost" onClick={startEditing}>
              <Edit3 className="h-4 w-4 mr-1" />Edit
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isEditing ? (
          <div className="space-y-2">
            {editMode === "write" ? (
              <Textarea
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Describe the approach..."
                className="min-h-[150px] font-mono text-sm"
                autoFocus
              />
            ) : (
              <div className="min-h-[150px] p-3 border rounded-md bg-muted/30">
                {editValue ? <Markdown>{editValue}</Markdown> : <p className="text-muted-foreground italic">Nothing to preview</p>}
              </div>
            )}
            <p className="text-xs text-muted-foreground">Markdown supported. Ctrl/Cmd + Enter to save.</p>
          </div>
        ) : (
          <div className="cursor-pointer hover:bg-muted/30 rounded-md p-2 -m-2" onClick={startEditing}>
            <Markdown>{plan.approach}</Markdown>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ============================================================================
// Sub-Tasks Section
// ============================================================================
function SubTasksSection({ task, plan }: { task: Task; plan: TaskPlan }) {
  const updateTask = useUpdateTask();
  const subTasks = plan.sub_tasks;

  const [isAdding, setIsAdding] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");

  const inputRef = useRef<HTMLInputElement>(null);
  const editRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isAdding && inputRef.current) inputRef.current.focus();
  }, [isAdding]);

  useEffect(() => {
    if (editingId && editRef.current) {
      editRef.current.focus();
      editRef.current.select();
    }
  }, [editingId]);

  const completedCount = subTasks.filter((st) => st.completed).length;

  const updatePlan = async (newSubTasks: SubTask[]) => {
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { plan: { ...plan, sub_tasks: newSubTasks } },
      });
    } catch {
      toast.error("Failed to update sub-tasks");
    }
  };

  const handleToggle = async (id: string) => {
    const newSubTasks = subTasks.map((st) =>
      st.id === id ? { ...st, completed: !st.completed } : st
    );
    await updatePlan(newSubTasks);
  };

  const handleAdd = async () => {
    if (!newTitle.trim()) {
      setIsAdding(false);
      return;
    }
    const newSubTask: SubTask = {
      id: generateId(),
      title: newTitle.trim(),
      description: null,
      completed: false,
      order: subTasks.length,
      estimated_hours: null,
      notes: null,
    };
    await updatePlan([...subTasks, newSubTask]);
    setNewTitle("");
  };

  const handleEdit = async (id: string) => {
    if (!editTitle.trim()) {
      await handleDelete(id);
      return;
    }
    const newSubTasks = subTasks.map((st) =>
      st.id === id ? { ...st, title: editTitle.trim() } : st
    );
    await updatePlan(newSubTasks);
    setEditingId(null);
  };

  const handleDelete = async (id: string) => {
    const newSubTasks = subTasks.filter((st) => st.id !== id);
    await updatePlan(newSubTasks);
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <ListChecks className="h-5 w-5" />
            Sub-Tasks
          </CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">{completedCount}/{subTasks.length} completed</span>
            {!isAdding && (
              <Button size="sm" variant="ghost" onClick={() => setIsAdding(true)}>
                <Plus className="h-4 w-4 mr-1" />Add
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {subTasks
            .sort((a, b) => a.order - b.order)
            .map((subtask) => (
              <div key={subtask.id} className="flex items-center gap-3 py-2 group">
                <Checkbox
                  checked={subtask.completed}
                  onCheckedChange={() => handleToggle(subtask.id)}
                  disabled={updateTask.isPending}
                />
                {editingId === subtask.id ? (
                  <div className="flex-1 flex items-center gap-2">
                    <Input
                      ref={editRef}
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleEdit(subtask.id);
                        if (e.key === "Escape") setEditingId(null);
                      }}
                      onBlur={() => handleEdit(subtask.id)}
                      className="h-8 text-sm"
                    />
                  </div>
                ) : (
                  <>
                    <span
                      className={`flex-1 cursor-pointer hover:bg-muted/30 px-2 py-1 -mx-2 rounded ${
                        subtask.completed ? "line-through text-muted-foreground" : ""
                      }`}
                      onClick={() => {
                        setEditingId(subtask.id);
                        setEditTitle(subtask.title);
                      }}
                    >
                      {subtask.title}
                      {subtask.estimated_hours && (
                        <Badge variant="outline" className="ml-2 text-xs">~{subtask.estimated_hours}h</Badge>
                      )}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleDelete(subtask.id)}
                      className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 text-destructive"
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </>
                )}
              </div>
            ))}
          {isAdding && (
            <div className="flex items-center gap-3 py-2">
              <Checkbox checked={false} disabled />
              <Input
                ref={inputRef}
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAdd();
                  if (e.key === "Escape") {
                    setNewTitle("");
                    setIsAdding(false);
                  }
                }}
                placeholder="Add sub-task..."
                className="h-8 text-sm flex-1"
              />
              <Button size="sm" variant="ghost" onClick={() => { setNewTitle(""); setIsAdding(false); }} className="h-7 w-7 p-0">
                <X className="h-4 w-4" />
              </Button>
              <Button size="sm" onClick={handleAdd} disabled={!newTitle.trim()} className="h-7 w-7 p-0">
                <Check className="h-4 w-4" />
              </Button>
            </div>
          )}
          {subTasks.length === 0 && !isAdding && (
            <p className="text-muted-foreground italic cursor-pointer hover:bg-muted/30 p-2 rounded" onClick={() => setIsAdding(true)}>
              No sub-tasks. Click to add one.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ============================================================================
// Technical Considerations Section
// ============================================================================
function TechConsiderationsSection({ task, plan }: { task: Task; plan: TaskPlan }) {
  const updateTask = useUpdateTask();
  const items = plan.technical_considerations;

  const [isAdding, setIsAdding] = useState(false);
  const [newItem, setNewItem] = useState("");
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");

  const inputRef = useRef<HTMLInputElement>(null);
  const editRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isAdding && inputRef.current) inputRef.current.focus();
  }, [isAdding]);

  useEffect(() => {
    if (editingIdx !== null && editRef.current) {
      editRef.current.focus();
      editRef.current.select();
    }
  }, [editingIdx]);

  const updatePlan = async (newItems: string[]) => {
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { plan: { ...plan, technical_considerations: newItems } },
      });
    } catch {
      toast.error("Failed to update technical considerations");
    }
  };

  const handleAdd = async () => {
    if (!newItem.trim()) {
      setIsAdding(false);
      return;
    }
    await updatePlan([...items, newItem.trim()]);
    setNewItem("");
  };

  const handleEdit = async (idx: number) => {
    if (!editValue.trim()) {
      await handleDelete(idx);
      return;
    }
    const newItems = [...items];
    newItems[idx] = editValue.trim();
    await updatePlan(newItems);
    setEditingIdx(null);
  };

  const handleDelete = async (idx: number) => {
    const newItems = items.filter((_, i) => i !== idx);
    await updatePlan(newItems);
    setEditingIdx(null);
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <Lightbulb className="h-5 w-5" />
            Technical Considerations
          </CardTitle>
          {!isAdding && (
            <Button size="sm" variant="ghost" onClick={() => setIsAdding(true)}>
              <Plus className="h-4 w-4 mr-1" />Add
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2">
          {items.map((item, idx) => (
            <li key={idx} className="flex items-start gap-2 group">
              <span className="mt-2 h-1.5 w-1.5 rounded-full bg-foreground shrink-0" />
              {editingIdx === idx ? (
                <div className="flex-1 flex items-center gap-2">
                  <Input
                    ref={editRef}
                    value={editValue}
                    onChange={(e) => setEditValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleEdit(idx);
                      if (e.key === "Escape") setEditingIdx(null);
                    }}
                    onBlur={() => handleEdit(idx)}
                    className="h-8 text-sm"
                  />
                </div>
              ) : (
                <>
                  <span
                    className="flex-1 text-sm cursor-pointer hover:bg-muted/30 px-2 py-1 -mx-2 rounded"
                    onClick={() => {
                      setEditingIdx(idx);
                      setEditValue(item);
                    }}
                  >
                    {item}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleDelete(idx)}
                    className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 text-destructive"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </>
              )}
            </li>
          ))}
          {isAdding && (
            <li className="flex items-center gap-2">
              <span className="mt-2 h-1.5 w-1.5 rounded-full bg-foreground shrink-0" />
              <Input
                ref={inputRef}
                value={newItem}
                onChange={(e) => setNewItem(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAdd();
                  if (e.key === "Escape") {
                    setNewItem("");
                    setIsAdding(false);
                  }
                }}
                placeholder="Add consideration..."
                className="h-8 text-sm flex-1"
              />
              <Button size="sm" variant="ghost" onClick={() => { setNewItem(""); setIsAdding(false); }} className="h-7 w-7 p-0">
                <X className="h-4 w-4" />
              </Button>
              <Button size="sm" onClick={handleAdd} disabled={!newItem.trim()} className="h-7 w-7 p-0">
                <Check className="h-4 w-4" />
              </Button>
            </li>
          )}
        </ul>
        {items.length === 0 && !isAdding && (
          <p className="text-muted-foreground italic cursor-pointer hover:bg-muted/30 p-2 rounded" onClick={() => setIsAdding(true)}>
            No technical considerations. Click to add one.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ============================================================================
// Risks Section
// ============================================================================
function RisksSection({ task, plan }: { task: Task; plan: TaskPlan }) {
  const updateTask = useUpdateTask();
  const risks = plan.risks;

  const [isAdding, setIsAdding] = useState(false);
  const [newDesc, setNewDesc] = useState("");
  const [newMit, setNewMit] = useState("");
  const [newSeverity, setNewSeverity] = useState("medium");
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editDesc, setEditDesc] = useState("");
  const [editMit, setEditMit] = useState("");
  const [editSeverity, setEditSeverity] = useState("medium");

  const descRef = useRef<HTMLInputElement>(null);
  const editDescRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isAdding && descRef.current) descRef.current.focus();
  }, [isAdding]);

  useEffect(() => {
    if (editingIdx !== null && editDescRef.current) {
      editDescRef.current.focus();
    }
  }, [editingIdx]);

  const updatePlan = async (newRisks: typeof risks) => {
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { plan: { ...plan, risks: newRisks } },
      });
    } catch {
      toast.error("Failed to update risks");
    }
  };

  const handleAdd = async () => {
    if (!newDesc.trim()) {
      setIsAdding(false);
      return;
    }
    await updatePlan([...risks, { description: newDesc.trim(), mitigation: newMit.trim(), severity: newSeverity }]);
    setNewDesc("");
    setNewMit("");
    setNewSeverity("medium");
  };

  const handleEdit = async () => {
    if (editingIdx === null) return;
    if (!editDesc.trim()) {
      await handleDelete(editingIdx);
      return;
    }
    const newRisks = [...risks];
    newRisks[editingIdx] = { description: editDesc.trim(), mitigation: editMit.trim(), severity: editSeverity };
    await updatePlan(newRisks);
    setEditingIdx(null);
  };

  const handleDelete = async (idx: number) => {
    const newRisks = risks.filter((_, i) => i !== idx);
    await updatePlan(newRisks);
    setEditingIdx(null);
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <AlertTriangle className="h-5 w-5" />
            Risks
          </CardTitle>
          {!isAdding && (
            <Button size="sm" variant="ghost" onClick={() => setIsAdding(true)}>
              <Plus className="h-4 w-4 mr-1" />Add
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {risks.map((risk, idx) =>
            editingIdx === idx ? (
              <div key={idx} className="border rounded-lg p-4 space-y-3">
                <Input
                  ref={editDescRef}
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  placeholder="Risk description..."
                  className="text-sm"
                />
                <Input
                  value={editMit}
                  onChange={(e) => setEditMit(e.target.value)}
                  placeholder="Mitigation strategy..."
                  className="text-sm"
                />
                <div className="flex items-center gap-2">
                  <Select value={editSeverity} onValueChange={setEditSeverity}>
                    <SelectTrigger className="w-32 h-8">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="low">Low</SelectItem>
                      <SelectItem value="medium">Medium</SelectItem>
                      <SelectItem value="high">High</SelectItem>
                    </SelectContent>
                  </Select>
                  <div className="flex-1" />
                  <Button size="sm" variant="ghost" onClick={() => setEditingIdx(null)}><X className="h-4 w-4" /></Button>
                  <Button size="sm" onClick={handleEdit}><Check className="h-4 w-4" /></Button>
                </div>
              </div>
            ) : (
              <div
                key={idx}
                className="border rounded-lg p-4 cursor-pointer hover:bg-muted/30 transition-colors group"
                onClick={() => {
                  setEditingIdx(idx);
                  setEditDesc(risk.description);
                  setEditMit(risk.mitigation);
                  setEditSeverity(risk.severity ?? "medium");
                }}
              >
                <div className="flex items-start justify-between gap-2 mb-2">
                  <span className="font-medium text-sm">{risk.description}</span>
                  <div className="flex items-center gap-2">
                    {risk.severity && <Badge className={severityColors[risk.severity]}>{risk.severity}</Badge>}
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={(e) => { e.stopPropagation(); handleDelete(idx); }}
                      className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 text-destructive"
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
                <div className="text-sm text-muted-foreground">
                  <span className="font-medium">Mitigation:</span> {risk.mitigation || "Not specified"}
                </div>
              </div>
            )
          )}
          {isAdding && (
            <div className="border rounded-lg p-4 space-y-3">
              <Input
                ref={descRef}
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                placeholder="Risk description..."
                className="text-sm"
              />
              <Input
                value={newMit}
                onChange={(e) => setNewMit(e.target.value)}
                placeholder="Mitigation strategy..."
                className="text-sm"
              />
              <div className="flex items-center gap-2">
                <Select value={newSeverity} onValueChange={setNewSeverity}>
                  <SelectTrigger className="w-32 h-8">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="low">Low</SelectItem>
                    <SelectItem value="medium">Medium</SelectItem>
                    <SelectItem value="high">High</SelectItem>
                  </SelectContent>
                </Select>
                <div className="flex-1" />
                <Button size="sm" variant="ghost" onClick={() => { setNewDesc(""); setNewMit(""); setIsAdding(false); }}><X className="h-4 w-4" /></Button>
                <Button size="sm" onClick={handleAdd} disabled={!newDesc.trim()}><Check className="h-4 w-4" /></Button>
              </div>
            </div>
          )}
        </div>
        {risks.length === 0 && !isAdding && (
          <p className="text-muted-foreground italic cursor-pointer hover:bg-muted/30 p-2 rounded" onClick={() => setIsAdding(true)}>
            No risks identified. Click to add one.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ============================================================================
// Open Questions Section
// ============================================================================
function OpenQuestionsSection({ task, plan }: { task: Task; plan: TaskPlan }) {
  const updateTask = useUpdateTask();
  const questions = plan.open_questions;

  const [isAdding, setIsAdding] = useState(false);
  const [newQuestion, setNewQuestion] = useState("");
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editQuestion, setEditQuestion] = useState("");
  const [editAnswer, setEditAnswer] = useState("");

  const inputRef = useRef<HTMLInputElement>(null);
  const editRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isAdding && inputRef.current) inputRef.current.focus();
  }, [isAdding]);

  useEffect(() => {
    if (editingIdx !== null && editRef.current) editRef.current.focus();
  }, [editingIdx]);

  const updatePlan = async (newQuestions: typeof questions) => {
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { plan: { ...plan, open_questions: newQuestions } },
      });
    } catch {
      toast.error("Failed to update open questions");
    }
  };

  const handleAdd = async () => {
    if (!newQuestion.trim()) {
      setIsAdding(false);
      return;
    }
    await updatePlan([...questions, { question: newQuestion.trim(), answer: null, answered_by: null, answered_at: null }]);
    setNewQuestion("");
  };

  const handleEdit = async () => {
    if (editingIdx === null) return;
    if (!editQuestion.trim()) {
      await handleDelete(editingIdx);
      return;
    }
    const newQuestions = [...questions];
    newQuestions[editingIdx] = {
      ...newQuestions[editingIdx],
      question: editQuestion.trim(),
      answer: editAnswer.trim() || null,
      answered_by: editAnswer.trim() ? "CEO" : null,
      answered_at: editAnswer.trim() ? new Date().toISOString() : null,
    };
    await updatePlan(newQuestions);
    setEditingIdx(null);
  };

  const handleDelete = async (idx: number) => {
    const newQuestions = questions.filter((_, i) => i !== idx);
    await updatePlan(newQuestions);
    setEditingIdx(null);
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <HelpCircle className="h-5 w-5" />
            Open Questions
          </CardTitle>
          {!isAdding && (
            <Button size="sm" variant="ghost" onClick={() => setIsAdding(true)}>
              <Plus className="h-4 w-4 mr-1" />Add
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {questions.map((q, idx) =>
            editingIdx === idx ? (
              <div key={idx} className="border rounded-lg p-4 space-y-3">
                <Input
                  ref={editRef}
                  value={editQuestion}
                  onChange={(e) => setEditQuestion(e.target.value)}
                  placeholder="Question..."
                  className="text-sm"
                />
                <Textarea
                  value={editAnswer}
                  onChange={(e) => setEditAnswer(e.target.value)}
                  placeholder="Answer (optional)..."
                  className="text-sm min-h-[80px]"
                />
                <div className="flex items-center gap-2">
                  <div className="flex-1" />
                  <Button size="sm" variant="ghost" onClick={() => setEditingIdx(null)}><X className="h-4 w-4" /></Button>
                  <Button size="sm" onClick={handleEdit}><Check className="h-4 w-4" /></Button>
                </div>
              </div>
            ) : (
              <div
                key={idx}
                className="border rounded-lg p-4 cursor-pointer hover:bg-muted/30 transition-colors group"
                onClick={() => {
                  setEditingIdx(idx);
                  setEditQuestion(q.question);
                  setEditAnswer(q.answer ?? "");
                }}
              >
                <div className="flex items-start gap-2 mb-2">
                  <HelpCircle className="h-5 w-5 text-yellow-500 shrink-0 mt-0.5" />
                  <span className="font-medium text-sm flex-1">{q.question}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={(e) => { e.stopPropagation(); handleDelete(idx); }}
                    className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 text-destructive"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
                {q.answer ? (
                  <div className="ml-7 text-sm">
                    <div className="bg-green-50 dark:bg-green-950 text-green-800 dark:text-green-200 rounded-lg p-3">
                      <p className="font-medium mb-1">Answered by {q.answered_by?.slice(0, 12) ?? "Unknown"}</p>
                      <p>{q.answer}</p>
                    </div>
                  </div>
                ) : (
                  <div className="ml-7">
                    <Badge variant="outline" className="text-yellow-600 border-yellow-300">Awaiting Answer</Badge>
                  </div>
                )}
              </div>
            )
          )}
          {isAdding && (
            <div className="border rounded-lg p-4 space-y-3">
              <Input
                ref={inputRef}
                value={newQuestion}
                onChange={(e) => setNewQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAdd();
                  if (e.key === "Escape") {
                    setNewQuestion("");
                    setIsAdding(false);
                  }
                }}
                placeholder="Add a question..."
                className="text-sm"
              />
              <div className="flex items-center gap-2">
                <div className="flex-1" />
                <Button size="sm" variant="ghost" onClick={() => { setNewQuestion(""); setIsAdding(false); }}><X className="h-4 w-4" /></Button>
                <Button size="sm" onClick={handleAdd} disabled={!newQuestion.trim()}><Check className="h-4 w-4" /></Button>
              </div>
            </div>
          )}
        </div>
        {questions.length === 0 && !isAdding && (
          <p className="text-muted-foreground italic cursor-pointer hover:bg-muted/30 p-2 rounded" onClick={() => setIsAdding(true)}>
            No open questions. Click to add one.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ============================================================================
// Main TabPlan Component
// ============================================================================
export function TabPlan({ task }: TabPlanProps) {
  const updateTask = useUpdateTask();
  const plan = task.plan;

  // Create empty plan if none exists
  const createPlan = async () => {
    const emptyPlan: TaskPlan = {
      approach: "",
      sub_tasks: [],
      technical_considerations: [],
      risks: [],
      open_questions: [],
    };
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { plan: emptyPlan },
      });
    } catch {
      toast.error("Failed to create plan");
    }
  };

  if (!plan) {
    return (
      <Card>
        <CardContent className="pt-6">
          <div className="text-center text-muted-foreground py-8">
            <FileText className="h-12 w-12 mx-auto mb-4 opacity-50" />
            <p>No implementation plan has been created yet.</p>
            <p className="text-sm mt-2 mb-4">
              A plan will be created once an agent claims and starts working on this task.
            </p>
            <Button onClick={createPlan} disabled={updateTask.isPending}>
              <Plus className="h-4 w-4 mr-2" />
              Create Plan
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <ApproachSection task={task} plan={plan} />
      <SubTasksSection task={task} plan={plan} />
      <TechConsiderationsSection task={task} plan={plan} />
      <RisksSection task={task} plan={plan} />
      <OpenQuestionsSection task={task} plan={plan} />
    </div>
  );
}
