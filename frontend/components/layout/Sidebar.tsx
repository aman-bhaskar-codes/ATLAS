"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Orbit, Activity, ListChecks, ShieldCheck, Brain, Waypoints, ScrollText, GraduationCap, Lightbulb, BarChart3, Wrench, Cpu, CalendarClock, Settings2, Zap, DollarSign } from "lucide-react";

export function Sidebar() {
  const pathname = usePathname();

  const groups = [
    {
      title: "COMMAND",
      links: [
        { href: "/", label: "Command Center", icon: Orbit },
      ]
    },
    {
      title: "RUNTIME",
      links: [
        { href: "/tasks/live", label: "Live Run", icon: Activity },
        { href: "/tasks", label: "Tasks", icon: ListChecks },
      ]
    },
    {
      title: "AUTONOMY",
      links: [
        { href: "/automations", label: "Automations", icon: Waypoints }, // using Waypoints or Zap
      ]
    },
    {
      title: "TRUST",
      links: [
        { href: "/approvals", label: "Approvals", icon: ShieldCheck },
        { href: "/audit", label: "Audit", icon: ScrollText },
      ]
    },
    {
      title: "INTELLIGENCE",
      links: [
        { href: "/memory", label: "Memory", icon: Brain },
        { href: "/capabilities", label: "Capabilities", icon: Waypoints },
      ]
    },
    {
      title: "LEARNING",
      links: [
        { href: "/skills", label: "Skills", icon: GraduationCap },
        { href: "/experiences", label: "Experiences", icon: Lightbulb },
        { href: "/analytics", label: "Analytics", icon: BarChart3 },
      ]
    },
    {
      title: "SYSTEM",
      links: [
        { href: "/providers", label: "Providers", icon: Zap },
        { href: "/cost", label: "Cost", icon: DollarSign },
        { href: "/tools", label: "Tools", icon: Wrench },
        { href: "/models", label: "Models", icon: Cpu },
        { href: "/schedules", label: "Schedules", icon: CalendarClock },
        { href: "/settings", label: "Settings", icon: Settings2 },
      ]
    }
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
      
      <nav className="nav" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', marginTop: '1rem' }}>
        {groups.map((group) => (
          <div key={group.title} style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            <div style={{ fontSize: '0.65rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--paper-500)', paddingLeft: '0.75rem', marginBottom: '0.25rem', fontWeight: 600 }}>
              {group.title}
            </div>
            {group.links.map((link) => {
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
          </div>
        ))}
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
