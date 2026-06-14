"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { Menu } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { SidebarNav, SidebarFooter } from "./sidebar";

/**
 * Mobile navigation: a hamburger button (shown only below md, where the static
 * sidebar is hidden) that opens the full nav in a left Sheet drawer. Reuses the
 * desktop SidebarNav/SidebarFooter so the two never drift, and closes itself on
 * navigation so the drawer doesn't linger over the new page.
 */
export function MobileSidebar() {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="md:hidden"
          aria-label="Open navigation menu"
        >
          <Menu className="h-5 w-5" />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="flex w-64 flex-col gap-0 p-0">
        <SheetHeader className="h-16 justify-center border-b px-4 text-left">
          <SheetTitle asChild>
            <Link
              href="/overview"
              onClick={close}
              className="flex items-center gap-2"
            >
              <Image
                src="/roboco-logo.png"
                alt="RoboCo"
                width={32}
                height={32}
                unoptimized
                className="h-8 w-8 rounded"
              />
              <span className="text-lg font-semibold">RoboCo</span>
            </Link>
          </SheetTitle>
        </SheetHeader>

        <ScrollArea className="flex-1 py-4">
          <SidebarNav onNavigate={close} />
        </ScrollArea>

        <div className="border-t p-2">
          <SidebarFooter onNavigate={close} />
        </div>
      </SheetContent>
    </Sheet>
  );
}
