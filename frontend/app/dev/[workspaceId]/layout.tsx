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
  return <>{children}</>;
}
