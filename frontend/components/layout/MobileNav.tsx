"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Orbit, Activity, ShieldCheck, Brain, Menu } from "lucide-react";

export function MobileNav() {
  const pathname = usePathname();

  return (
    <nav className="mobile-nav" aria-label="Mobile navigation">
      <Link href="/" className={pathname === "/" ? "active" : ""}>
        <Orbit /> Home
      </Link>
      <Link href="/tasks/live" className={pathname === "/tasks/live" ? "active" : ""}>
        <Activity /> Run
      </Link>
      <Link href="/approvals" className={pathname === "/approvals" ? "active" : ""}>
        <ShieldCheck /> Approve
      </Link>
      <Link href="/memory" className={pathname === "/memory" ? "active" : ""}>
        <Brain /> Memory
      </Link>
      <details className="mobile-more">
        <summary><Menu /> More</summary>
        <div className="mobile-more-links" onClick={() => { const menu = document.querySelector<HTMLDetailsElement>(".mobile-more"); if (menu) menu.open = false; }}>
          <Link href="/research">Research</Link><Link href="/workspaces">IDE</Link>
          <Link href="/models">Models</Link><Link href="/tools">Tools</Link>
          <Link href="/mcp">MCP</Link><Link href="/settings">Settings</Link>
        </div>
      </details>
    </nav>
  );
}
