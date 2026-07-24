"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  ListTodo,
  Activity,
  ChevronLeft,
  Settings,
  Bot,
  Shield,
  Briefcase,
  GitBranch,
  Database,
  Cpu,
  Sparkles,
  Building2,
  Share2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useUIStore } from "@/store";

// Flat list, in the exact order product wants the sidebar to read top to
// bottom. Notifications lives only in the header's NotificationBell now.
// `tip` doubles as the collapsed icon-rail tooltip and the expanded-row
// hover hint — one description, both surfaces.
export const navItems = [
  {
    title: "Overview",
    href: "/overview",
    icon: LayoutDashboard,
    tip: "Company-wide dashboard: key metrics, blockers, and approval queues",
  },
  {
    title: "Task Assistant",
    href: "/prompter",
    icon: Sparkles,
    tip: "Chat with Intake to draft and confirm new tasks, including MegaTask batches",
  },
  {
    title: "Tasks",
    href: "/tasks",
    icon: ListTodo,
    tip: "Full task list — filter, search, and open any task's detail",
  },
  {
    title: "Git",
    href: "/git",
    icon: GitBranch,
    tip: "Branches, commits, and diffs across every project workspace",
  },
  {
    title: "Workstation",
    href: "/workstation",
    icon: Briefcase,
    tip: "Products the fleet ships against, and the repos/projects behind them",
  },
  {
    title: "Social",
    href: "/social",
    icon: Share2,
    tip: "X and TikTok post queues, plus the video pipeline",
  },
  {
    title: "Knowledge Base",
    href: "/knowledge-base",
    icon: Database,
    tip: "Search the RAG corpus — playbooks, learnings, and vault notes",
  },
  {
    title: "Agents",
    href: "/agents",
    icon: Bot,
    tip: "Every agent's live state, spawn controls, A2A conversations, and journals",
  },
  {
    title: "Auditor",
    href: "/auditor",
    icon: Shield,
    tip: "Silent-observer quality flags and findings review queue",
  },
  {
    title: "Metrics",
    href: "/metrics",
    icon: Activity,
    tip: "Performance, token usage/cost, delivery, and scorecard analytics",
  },
];

// Business moved out of the main nav — it lives with the settings-adjacent
// links, separated from navItems by a single Separator (see SidebarFooter).
const footerItems = [
  {
    title: "Business",
    href: "/business",
    icon: Building2,
    tip: "Company goals, roadmap proposals, pitches, and secretary directives",
  },
  {
    title: "AI Providers",
    href: "/settings/ai-providers",
    icon: Cpu,
    tip: "Model routing and per-role provider assignments",
  },
  {
    title: "Settings",
    href: "/settings",
    icon: Settings,
    tip: "Feature flags, credentials, and panel preferences",
  },
];

/**
 * The navigation links, shared by the desktop sidebar and the mobile Sheet
 * drawer so both stay in sync. `collapsed` hides labels (desktop rail);
 * `onNavigate` lets the mobile drawer close itself when a link is tapped.
 */
export function SidebarNav({
  collapsed = false,
  onNavigate,
}: {
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  return (
    <nav className="space-y-1 px-2">
      {navItems.map((item) => {
        const isActive = pathname.startsWith(item.href);
        const link = (
          <Link
            prefetch={false}
            href={item.href}
            onClick={onNavigate}
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              isActive
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
              collapsed && "justify-center px-2",
            )}
          >
            <item.icon className="h-5 w-5 shrink-0" />
            {!collapsed && <span>{item.title}</span>}
          </Link>
        );
        // Collapsed rail: tooltip is the only place the destination is named.
        // Expanded row: the label is already visible, so the tooltip instead
        // says what lives there — plain Link, safe to wrap (no stateful
        // Radix trigger to clobber).
        return (
          <Tooltip key={item.href}>
            <TooltipTrigger asChild>{link}</TooltipTrigger>
            <TooltipContent side="right">
              {collapsed ? item.title : item.tip}
            </TooltipContent>
          </Tooltip>
        );
      })}
    </nav>
  );
}

/** Footer links (AI Providers, Settings), shared by desktop + mobile. */
export function SidebarFooter({
  collapsed = false,
  onNavigate,
}: {
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  // No Separator here: both wrappers (desktop aside + mobile Sheet) already
  // draw a border-t, and a second line reads as a rendering glitch.
  return (
    <div className="space-y-1">
      {footerItems.map((item) => {
        // Exact match: /settings must not also highlight on /settings/ai-providers.
        const isActive = pathname === item.href;
        const link = (
          <Link
            prefetch={false}
            href={item.href}
            onClick={onNavigate}
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              isActive
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
              collapsed && "justify-center px-2",
            )}
          >
            <item.icon className="h-5 w-5" />
            {!collapsed && <span>{item.title}</span>}
          </Link>
        );
        return (
          <Tooltip key={item.href}>
            <TooltipTrigger asChild>{link}</TooltipTrigger>
            <TooltipContent side="right">
              {collapsed ? item.title : item.tip}
            </TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}

export function Sidebar() {
  const { sidebarCollapsed, setSidebarCollapsed } = useUIStore();
  const toggleLabel = sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar";

  return (
    <aside
      className={cn(
        // Hidden on mobile — the Header's hamburger opens the same nav in a
        // Sheet drawer there (see MobileSidebar). Shown from md upward.
        "hidden h-screen flex-col border-r bg-background transition-all duration-300 md:flex",
        sidebarCollapsed ? "w-16" : "w-64",
      )}
    >
      {/* Logo */}
      <div className="flex h-16 items-center justify-between border-b px-4">
        {!sidebarCollapsed && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Link
                href="/overview"
                className="flex items-center gap-2"
                prefetch={false}
              >
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
            </TooltipTrigger>
            <TooltipContent side="bottom">Back to Overview</TooltipContent>
          </Tooltip>
        )}
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
                className={cn(sidebarCollapsed && "mx-auto")}
                aria-label={toggleLabel}
                title={toggleLabel}
              >
                <ChevronLeft
                  className={cn(
                    "h-4 w-4 transition-transform",
                    sidebarCollapsed && "rotate-180",
                  )}
                />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{toggleLabel}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>

      {/* Navigation */}
      <ScrollArea className="flex-1 py-4">
        <SidebarNav collapsed={sidebarCollapsed} />
      </ScrollArea>

      {/* Footer */}
      <div className="border-t p-2">
        <SidebarFooter collapsed={sidebarCollapsed} />
      </div>
    </aside>
  );
}
