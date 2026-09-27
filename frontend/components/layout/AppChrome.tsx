"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { MobileNav } from "@/components/layout/MobileNav";
import { CommandPalette } from "@/components/command/CommandPalette";

/**
 * Chrome partition (§94). The Command Center and the Development Workbench are two
 * distinct surfaces that happen to share one root layout (a single `<html>/<body>`
 * so React state and the query cache survive client navigations between them).
 *
 * `/dev/[workspaceId]` is a full-viewport workbench that owns its own chrome
 * (ActivityBar / StatusBar / Agent panel) and must NOT nest under the global
 * Sidebar/Topbar/MobileNav or the Command Center's ⌘K palette — those belong to the
 * Command Center only. Rather than fragment every route into parallel route groups
 * (a 25-folder move with its own risk), we branch here on the pathname: the
 * workbench renders bare, every other route gets the full Command Center frame.
 *
 * `usePathname` is consistent across SSR and hydration in the App Router, so this
 * branch does not cause a hydration mismatch.
 */
export function AppChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isWorkbench = pathname === "/dev" || pathname.startsWith("/dev/");
  // Only a research SESSION (/research/<id>) is bare; /research stays framed.
  const isResearchSession = pathname.startsWith("/research/");

  if (isWorkbench || isResearchSession) {
    return <>{children}</>;
  }

  return (
    <>
      <div className="app">
        <Sidebar />
        <main className="main">
          <Topbar />
          {children}
        </main>
      </div>
      <MobileNav />
      <CommandPalette />
    </>
  );
}
