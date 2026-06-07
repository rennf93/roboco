"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  ListTodo,
  Kanban,
  MessageSquare,
  Bell,
  Activity,
  ChevronLeft,
  Settings,
  Bot,
  Shield,
  BookOpen,
  Boxes,
  FolderGit2,
  GitBranch,
  Database,
  Cpu,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useUIStore } from "@/store";

const navItems = [
  // Dashboard
  { title: "Overview", href: "/overview", icon: LayoutDashboard },

  // Work Management
  { title: "Tasks", href: "/tasks", icon: ListTodo },
  { title: "Kanban", href: "/kanban", icon: Kanban },
  { title: "Task Assistant", href: "/prompter", icon: Sparkles },

  // Development
  { title: "Projects", href: "/projects", icon: FolderGit2 },
  { title: "Products", href: "/products", icon: Boxes },
  { title: "Git", href: "/git", icon: GitBranch },

  // Team & Reference
  { title: "Agents", href: "/agents", icon: Bot },
  { title: "Knowledge Base", href: "/knowledge-base", icon: Database },
  { title: "Auditor", href: "/auditor", icon: Shield },

  // History
  { title: "Communications", href: "/communications", icon: MessageSquare },
  { title: "Journals", href: "/journals", icon: BookOpen },

  // System
  { title: "Notifications", href: "/notifications", icon: Bell },
  { title: "Metrics", href: "/metrics", icon: Activity },
];

export function Sidebar() {
  const pathname = usePathname();
  const { sidebarCollapsed, setSidebarCollapsed } = useUIStore();

  return (
    <aside
      className={cn(
        "flex h-screen flex-col border-r bg-background transition-all duration-300",
        sidebarCollapsed ? "w-16" : "w-64"
      )}
    >
      {/* Logo */}
      <div className="flex h-16 items-center justify-between border-b px-4">
        {!sidebarCollapsed && (
          <Link href="/overview" className="flex items-center gap-2">
            <Image
              src="/roboco-logo.png"
              alt="RoboCo"
              width={32}
              height={32}
              priority
              unoptimized
              className="h-8 w-8 rounded"
            />
            <span className="font-semibold text-lg">RoboCo</span>
          </Link>
        )}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          className={cn(sidebarCollapsed && "mx-auto")}
        >
          <ChevronLeft
            className={cn(
              "h-4 w-4 transition-transform",
              sidebarCollapsed && "rotate-180"
            )}
          />
        </Button>
      </div>

      {/* Navigation */}
      <ScrollArea className="flex-1 py-4">
        <nav className="space-y-1 px-2">
          {navItems.map((item) => {
            const isActive = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  sidebarCollapsed && "justify-center px-2"
                )}
                title={sidebarCollapsed ? item.title : undefined}
              >
                <item.icon className="h-5 w-5 shrink-0" />
                {!sidebarCollapsed && <span>{item.title}</span>}
              </Link>
            );
          })}
        </nav>
      </ScrollArea>

      {/* Footer */}
      <div className="border-t p-2 space-y-1">
        <Link
          href="/settings/ai-providers"
          className={cn(
            "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition-colors",
            sidebarCollapsed && "justify-center px-2"
          )}
          title={sidebarCollapsed ? "AI Providers" : undefined}
        >
          <Cpu className="h-5 w-5" />
          {!sidebarCollapsed && <span>AI Providers</span>}
        </Link>
        <Link
          href="/settings"
          className={cn(
            "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition-colors",
            sidebarCollapsed && "justify-center px-2"
          )}
          title={sidebarCollapsed ? "Settings" : undefined}
        >
          <Settings className="h-5 w-5" />
          {!sidebarCollapsed && <span>Settings</span>}
        </Link>
      </div>
    </aside>
  );
}
