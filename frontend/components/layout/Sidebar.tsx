"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import {
  Orbit, Activity, ListChecks, PlayCircle, ShieldCheck, Brain, Boxes,
  Waypoints, ScrollText, GraduationCap, Lightbulb, BarChart3, Wrench, Cpu,
  CalendarClock, Settings2, Zap, DollarSign, Library, FlaskConical, LayoutGrid,
  ChevronDown, ChevronRight,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

interface NavItem {
  label: string;
  icon: LucideIcon;
  href?: string;        // present => real, navigable route
  reason?: string;      // present => deliberately disabled, with a visible reason
}

interface NavGroup {
  title: string;
  items: NavItem[];
  collapsible?: boolean; // progressive disclosure for advanced surfaces
}

const GROUPS: NavGroup[] = [
  {
    title: "COMMAND",
    items: [{ href: "/", label: "Home", icon: Orbit }],
  },
  {
    title: "RUNTIME",
    items: [
      { href: "/tasks/live", label: "Live Run", icon: PlayCircle },
      { href: "/tasks", label: "Tasks", icon: ListChecks },
      { href: "/events/search", label: "Activity", icon: Activity },
    ],
  },
  {
    title: "AUTONOMY",
    items: [{ href: "/automations", label: "Automations", icon: Waypoints }],
  },
  {
    title: "TRUST & SAFETY",
    items: [
      { href: "/approvals", label: "Approvals", icon: ShieldCheck },
      { href: "/audit", label: "Audit", icon: ScrollText },
    ],
  },
  {
    title: "INTELLIGENCE",
    items: [
      { href: "/memory", label: "Memory", icon: Brain },
      { href: "/capabilities", label: "Capabilities", icon: Boxes },
      { label: "Knowledge", icon: Library, reason: "Knowledge fabric UI not yet available" },
    ],
  },
  {
    title: "RESEARCH",
    items: [
      { label: "Research", icon: FlaskConical, reason: "Research workspace not yet available" },
      { label: "Workspaces", icon: LayoutGrid, reason: "Workspaces not yet available" },
    ],
  },
  {
    title: "LEARNING",
    items: [
      { href: "/skills", label: "Skills", icon: GraduationCap },
      { href: "/experiences", label: "Experiences", icon: Lightbulb },
      { href: "/analytics", label: "Analytics", icon: BarChart3 },
    ],
  },
  {
    title: "SYSTEM",
    collapsible: true,
    items: [
      { href: "/providers", label: "Providers", icon: Zap },
      { href: "/cost", label: "Cost", icon: DollarSign },
      { href: "/tools", label: "Tools", icon: Wrench },
      { href: "/models", label: "Models", icon: Cpu },
      { href: "/schedules", label: "Schedules", icon: CalendarClock },
      { href: "/settings", label: "Settings", icon: Settings2 },
    ],
  },
];

const GROUP_LABEL_STYLE: React.CSSProperties = {
  fontSize: "0.65rem",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  color: "var(--paper-500)",
  paddingLeft: "0.75rem",
  marginBottom: "0.25rem",
  fontWeight: 600,
};

function DisabledItem({ item }: { item: NavItem }) {
  const Icon = item.icon;
  return (
    <span
      aria-disabled="true"
      title={item.reason}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.6rem",
        padding: "0.4rem 0.75rem",
        color: "var(--paper-500)",
        opacity: 0.55,
        cursor: "not-allowed",
        fontSize: "0.85rem",
      }}
    >
      <Icon />
      <span style={{ flex: 1 }}>{item.label}</span>
      <span
        style={{
          fontSize: "0.55rem",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          border: "1px solid var(--line)",
          borderRadius: "999px",
          padding: "0.05rem 0.4rem",
          color: "var(--paper-500)",
        }}
      >
        soon
      </span>
    </span>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  // Progressive disclosure: advanced groups start collapsed unless you're inside one.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    const state: Record<string, boolean> = {};
    for (const g of GROUPS) {
      if (g.collapsible) {
        const active = g.items.some((i) => i.href && i.href === pathname);
        state[g.title] = !active; // collapsed unless the active route lives here
      }
    }
    return state;
  });

  const toggle = (title: string) =>
    setCollapsed((prev) => ({ ...prev, [title]: !prev[title] }));

  return (
    <aside className="rail" aria-label="Primary navigation">
      <div className="brand">
        <div className="sigil">A</div>
        <div className="brand-copy">
          <div className="brand-name">ATLAS</div>
          <div className="brand-sub">Private command layer</div>
        </div>
      </div>

      <nav
        className="nav"
        style={{ display: "flex", flexDirection: "column", gap: "1.25rem", marginTop: "1rem" }}
      >
        {GROUPS.map((group) => {
          const isCollapsed = group.collapsible ? collapsed[group.title] : false;
          return (
            <div key={group.title} style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
              {group.collapsible ? (
                <button
                  type="button"
                  onClick={() => toggle(group.title)}
                  aria-expanded={!isCollapsed}
                  style={{
                    ...GROUP_LABEL_STYLE,
                    display: "flex",
                    alignItems: "center",
                    gap: "0.35rem",
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  {isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                  {group.title}
                </button>
              ) : (
                <div style={GROUP_LABEL_STYLE}>{group.title}</div>
              )}

              {!isCollapsed &&
                group.items.map((item) => {
                  if (!item.href) return <DisabledItem key={item.label} item={item} />;
                  const Icon = item.icon;
                  const isActive = pathname === item.href;
                  return (
                    <Link key={item.href} href={item.href} className={isActive ? "active" : ""}>
                      <Icon />
                      <span>{item.label}</span>
                    </Link>
                  );
                })}
            </div>
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
