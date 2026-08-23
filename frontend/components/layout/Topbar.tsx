"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import { HeartPulse, Search, Ban, ShieldCheck } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";
import { ChromeBoundary } from "@/components/layout/ChromeBoundary";
import {
  useRuntimeStatus,
  deriveAtlasState,
  toneColor,
} from "@/features/runtime-console/useRuntimeStatus";

const CRUMBS: Record<string, string> = {
  "": "Command Center",
  tasks: "Tasks",
  approvals: "Approvals",
  audit: "Audit",
  memory: "Memory",
  capabilities: "Capabilities",
  automations: "Automations",
  analytics: "Analytics",
  skills: "Skills",
  experiences: "Experiences",
  providers: "Providers",
  cost: "Cost",
  tools: "Tools",
  models: "Models",
  schedules: "Schedules",
  settings: "Settings",
  events: "Activity",
};

function healthColor(overall: string | undefined): string {
  if (overall === "healthy") return "var(--jade-400)";
  if (overall === "degraded") return "var(--gold-400)";
  if (overall === "unavailable") return "var(--ember-400)";
  return "var(--paper-500)";
}

export function Topbar() {
  const pathname = usePathname();
  const { data: status, isError } = useRuntimeStatus();
  const [showHealth, setShowHealth] = useState(false);

  const { data: health, isError: healthError } = useQuery({
    queryKey: ["runtimeHealth"],
    queryFn: atlasApi.runtimeHealth,
    refetchInterval: 15000,
  });

  const seg = (pathname || "/").split("/").filter(Boolean)[0] ?? "";
  const crumb = CRUMBS[seg] ?? (seg ? seg.charAt(0).toUpperCase() + seg.slice(1) : "Command Center");

  const st = deriveAtlasState(status, isError);
  const killActive = status?.kill_switch_active ?? false;
  const failing = (health?.checks ?? []).filter((c) => c.status !== "pass");
  // A failed request is not the same fact as "no data yet", so it does not get the
  // neutral pre-first-poll grey — it reads as a real unavailable posture.
  const healthTone = healthError ? "unavailable" : health?.overall;

  return (
    <header className="topbar">
      <div className="crumb">
        ATLAS / <strong>{crumb}</strong>
      </div>
      <div className="top-actions" style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
        {/* Command palette launcher — dispatches the global open event */}
        <button
          className="ghost-btn"
          onClick={() => window.dispatchEvent(new CustomEvent("atlas:open-command-palette"))}
          aria-label="Open command palette"
          title="Command palette"
          style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem" }}
        >
          <Search width={16} height={16} />
          Search
          <kbd
            style={{
              fontSize: "0.6rem",
              border: "1px solid var(--line)",
              borderRadius: "4px",
              padding: "0.05rem 0.3rem",
              color: "var(--paper-500)",
            }}
          >
            ⌘K
          </kbd>
        </button>

        {/* ATLAS STATUS pill — derived only from real /runtime/status fields.
            Wrapped because the Topbar renders in the root layout, which
            `app/error.tsx` does not cover: an unhandled throw here would otherwise
            escalate to global-error.tsx and replace the whole application. */}
        <ChromeBoundary label="ATLAS status">
          <span
            title={st.detail}
            aria-label={`ATLAS status: ${st.label}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "0.45rem",
              padding: "0.3rem 0.6rem",
              borderRadius: "999px",
              border: "1px solid var(--line)",
              fontSize: "0.7rem",
              letterSpacing: "0.03em",
              color: "var(--paper-300)",
            }}
          >
            <span
              className="health-dot"
              style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: toneColor(st.tone) }}
            />
            {st.label}
          </span>
        </ChromeBoundary>

        {/* System health — real popover, no alert() */}
        <div style={{ position: "relative" }}>
          <button
            className="icon-btn"
            aria-label="System health"
            aria-expanded={showHealth}
            onClick={() => setShowHealth((s) => !s)}
          >
            <HeartPulse width={18} height={18} color={healthColor(healthTone)} />
          </button>
          {showHealth && (
            <div
              role="dialog"
              aria-label="System health detail"
              style={{
                position: "absolute",
                right: 0,
                top: "calc(100% + 8px)",
                width: 280,
                zIndex: 50,
                background: "var(--ink-900)",
                border: "1px solid var(--line)",
                borderRadius: "var(--radius-md)",
                padding: "0.75rem",
                boxShadow: "0 8px 24px oklch(10% 0.02 278 / 0.5)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                <span style={{ fontSize: "0.75rem", color: "var(--paper-300)" }}>System health</span>
                <span style={{ fontSize: "0.75rem", color: healthColor(healthTone), fontWeight: 600 }}>
                  {healthError ? "unreachable" : health?.overall ?? "…"}
                </span>
              </div>
              {failing.length === 0 ? (
                <div style={{ fontSize: "0.72rem", color: "var(--paper-500)" }}>
                  {/* Three cases, not two. `health ? … : "Loading checks…"` claimed a
                      request was still in flight when it had already failed, and
                      "All checks passing" is unsayable when no check was read. */}
                  {healthError
                    ? "Health checks could not be read — the backend did not answer."
                    : health
                      ? "All checks passing."
                      : "Loading checks…"}
                </div>
              ) : (
                <ul style={{ display: "flex", flexDirection: "column", gap: "0.4rem", margin: 0, padding: 0, listStyle: "none" }}>
                  {failing.map((c) => (
                    <li key={c.name} style={{ fontSize: "0.72rem" }}>
                      <span style={{ color: c.status === "fail" ? "var(--ember-400)" : "var(--gold-400)" }}>● </span>
                      <span style={{ color: "var(--paper-300)" }}>{c.name}</span>
                      <div style={{ color: "var(--paper-500)", paddingLeft: "0.9rem" }}>{c.detail}</div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        {/* Kill switch — READ-ONLY indicator. There is no HTTP trip endpoint by
            design; it is controlled via CLI/file. We show real state, never fake a POST. */}
        <span
          className={killActive ? "kill" : ""}
          title={
            killActive
              ? "Kill switch is ACTIVE — new actions are blocked. Controlled via CLI/file."
              : "Kill switch inactive. Armed via CLI/file (no web trip by design)."
          }
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "0.4rem",
            padding: "0.3rem 0.6rem",
            borderRadius: "999px",
            border: `1px solid ${killActive ? "var(--danger-400)" : "var(--line)"}`,
            fontSize: "0.7rem",
            color: killActive ? "var(--danger-400)" : "var(--paper-500)",
          }}
        >
          {killActive ? <Ban width={14} height={14} /> : <ShieldCheck width={14} height={14} />}
          {killActive ? "HALTED" : "Kill switch: off"}
        </span>
      </div>
    </header>
  );
}
