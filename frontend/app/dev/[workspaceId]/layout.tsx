import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "ATLAS | Development Workbench",
};

/**
 * Workbench route segment. `/dev/*` renders as a full-viewport surface that owns
 * its own chrome (ActivityBar / StatusBar / Agent panel); the global Command Center
 * Sidebar/Topbar/MobileNav/⌘K palette are branched off for this path in
 * `components/layout/AppChrome` (§94 chrome partition), so the workbench no longer
 * relies on overlaying that chrome. This layout owns the workbench's metadata.
 */
export default function DevLayout({ children }: { children: React.ReactNode }) {
  return <div className="ide-workbench-platform" data-platform="ide">
    <header className="ide-workbench-header" aria-label="IDE platform">
      <Link href="/workspaces">ATLAS IDE · Workspaces</Link>
      <Link href="/">Return to command centre</Link>
    </header>
    {children}
  </div>;
}
