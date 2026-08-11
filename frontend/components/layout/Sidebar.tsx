"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Orbit, Activity, ListChecks, ShieldCheck, Brain, Waypoints, ScrollText } from "lucide-react";

export function Sidebar() {
  const pathname = usePathname();

  const links = [
    { href: "/", label: "Command Center", icon: Orbit },
    { href: "/tasks/live", label: "Live Run", icon: Activity },
    { href: "/tasks", label: "Tasks", icon: ListChecks },
    { href: "/approvals", label: "Approvals", icon: ShieldCheck },
    { href: "/memory", label: "Memory", icon: Brain },
    { href: "/capabilities", label: "Capabilities", icon: Waypoints },
    { href: "/audit", label: "Audit", icon: ScrollText },
  ];

  return (
    <aside className="rail" aria-label="Primary navigation">
      <div className="brand">
        <div className="sigil">A</div>
        <div className="brand-copy">
          <div className="brand-name">ATLAS</div>
          <div className="brand-sub">Private command layer</div>
        </div>
      </div>
      
      <nav className="nav">
        {links.map((link) => {
          const Icon = link.icon;
          const isActive = pathname === link.href;
          return (
            <Link 
              key={link.href} 
              href={link.href} 
              className={isActive ? "active" : ""}
            >
              <Icon />
              <span>{link.label}</span>
            </Link>
          );
        })}
      </nav>
      
      <div className="rail-bottom">
        <div className="owner">
          <div className="avatar">A</div>
          <div className="owner-copy">
            <b>Aman</b>
            <small>owner identity</small>
          </div>
        </div>
      </div>
    </aside>
  );
}
