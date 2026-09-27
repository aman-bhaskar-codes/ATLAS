import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "ATLAS | Research",
};

/**
 * Research session route segment. `/research/[sessionId]` renders as a
 * full-viewport Perplexity-class surface that owns its own bare chrome (query
 * hero / streaming answer / source rail / follow-up composer); the global Command
 * Center Sidebar/Topbar/MobileNav/⌘K palette are branched off for this path in
 * `components/layout/AppChrome` (§94 chrome partition). This layout owns the
 * surface's metadata.
 *
 * NOTE: only `/research/<id>` is bare — `/research` itself stays IN the Command
 * Center frame as the composer + history launcher.
 */
export default function ResearchSessionLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
