"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  ListTodo,
  Kanban,
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
  Building2,
  Radio,
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
export const navItems = [
  { title: "Overview", href: "/overview", icon: LayoutDashboard },
  { title: "Task Assistant", href: "/prompter", icon: Sparkles },
  { title: "Tasks", href: "/tasks", icon: ListTodo },
  { title: "Kanban", href: "/kanban", icon: Kanban },
  { title: "Git", href: "/git", icon: GitBranch },
  { title: "Projects", href: "/projects", icon: FolderGit2 },
  { title: "Products", href: "/products", icon: Boxes },
  { title: "Social", href: "/social", icon: Share2 },
  { title: "Knowledge Base", href: "/knowledge-base", icon: Database },
  { title: "A2A", href: "/a2a", icon: Radio },
  { title: "Agents", href: "/agents", icon: Bot },
  { title: "Journals", href: "/journals", icon: BookOpen },
  { title: "Auditor", href: "/auditor", icon: Shield },
  { title: "Metrics", href: "/metrics", icon: Activity },
];

// Business moved out of the main nav — it lives with the settings-adjacent
// links, separated from navItems by a single Separator (see SidebarFooter).
const footerItems = [
  { title: "Business", href: "/business", icon: Building2 },
  { title: "AI Providers", href: "/settings/ai-providers", icon: Cpu },
  { title: "Settings", href: "/settings", icon: Settings },
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
        // Icon-only rail: the label moves into a tooltip.
        return collapsed ? (
          <Tooltip key={item.href}>
            <TooltipTrigger asChild>{link}</TooltipTrigger>
            <TooltipContent side="right">{item.title}</TooltipContent>
          </Tooltip>
        ) : (
          <span key={item.href} className="block">
            {link}
          </span>
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
        return collapsed ? (
          <Tooltip key={item.href}>
            <TooltipTrigger asChild>{link}</TooltipTrigger>
            <TooltipContent side="right">{item.title}</TooltipContent>
          </Tooltip>
        ) : (
          <span key={item.href} className="block">
            {link}
          </span>
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
