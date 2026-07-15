"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, ListTodo, Kanban, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { HelpTip } from "@/components/ui/help-tip";
import { navItems } from "./sidebar";

// Reuses navItems' descriptions (defined once in sidebar.tsx) so the two
// nav surfaces never drift out of sync.
function tipFor(href: string): string {
  return navItems.find((n) => n.href === href)?.tip ?? "";
}

const BOTTOM_NAV_ITEMS = [
  { title: "Overview", href: "/overview", icon: LayoutDashboard },
  { title: "Tasks", href: "/tasks", icon: ListTodo },
  { title: "Kanban", href: "/kanban", icon: Kanban },
  { title: "Chat", href: "/prompter", icon: Sparkles },
];

/**
 * Persistent one-thumb-reach bottom nav for the 4 most-used destinations,
 * alongside the full-nav drawer (MobileSidebar) — the drawer covers every
 * route, this covers the common loop without opening it. `md:hidden` mirrors
 * the sidebar's own breakpoint so exactly one nav surface is ever visible.
 */
export function BottomTabBar() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 flex border-t bg-background pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      {BOTTOM_NAV_ITEMS.map((item) => {
        const isActive = pathname.startsWith(item.href);
        return (
          <HelpTip key={item.href} label={tipFor(item.href)}>
            <Link
              href={item.href}
              prefetch={false}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "flex flex-1 flex-col items-center gap-0.5 py-2 text-xs font-medium transition-colors",
                isActive ? "text-primary" : "text-muted-foreground",
              )}
            >
              <item.icon className="h-5 w-5" />
              {item.title}
            </Link>
          </HelpTip>
        );
      })}
    </nav>
  );
}
