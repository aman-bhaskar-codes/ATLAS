"use client";

import { PlatformShell } from "./PlatformShell";
import { platformForPath } from "@/lib/platforms/navigation";
import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { MobileNav } from "@/components/layout/MobileNav";
import { CommandPalette } from "@/components/command/CommandPalette";

export function AppChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const platform = platformForPath(pathname);
  if (pathname === "/dev" || pathname.startsWith("/dev/")) return <>{children}</>;
  if (platform !== "command") return <PlatformShell platform={platform}>{children}</PlatformShell>;

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
