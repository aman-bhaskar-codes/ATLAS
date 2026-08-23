"use client";

import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";
import {
  useRuntimeStatus,
  deriveAtlasState,
  toneColor,
} from "@/features/runtime-console/useRuntimeStatus";
import { Ban } from "lucide-react";

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

export function HeroSection() {
  const { data: status, isError: statusError } = useRuntimeStatus();
  const { data: health, isError: healthError } = useQuery({
    queryKey: ["runtimeHealth"],
    queryFn: atlasApi.runtimeHealth,
    refetchInterval: 15000,
  });

  const st = deriveAtlasState(status, statusError);
  const posture = healthError ? "unavailable" : health?.overall ?? "…";
  const postureColorVar =
    posture === "healthy"
      ? "var(--jade-400)"
      : posture === "unavailable"
        ? "var(--ember-400)"
        : posture === "degraded"
          ? "var(--gold-400)"
          : "var(--paper-500)";

  const dateStr = new Intl.DateTimeFormat("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date());

  return (
    <section className="hero">
      <div>
        <div className="eyebrow">{dateStr} · local-first</div>
        <h1 className="display">{greeting()}, Aman.</h1>
        <p>ATLAS is local, quiet, and safety-governed. Ask for something — it will show its plan before it acts.</p>

        {status?.kill_switch_active && (
          <div
            role="alert"
            style={{
              marginTop: "1rem",
              display: "inline-flex",
              alignItems: "center",
              gap: "0.5rem",
              padding: "0.5rem 0.85rem",
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--danger-400)",
              color: "var(--danger-400)",
              fontSize: "0.8rem",
            }}
          >
            <Ban width={16} height={16} />
            Kill switch is active — new actions are blocked. Release it via the CLI/file control.
          </div>
        )}
      </div>

      <aside className="health">
        <div className="health-head">
          <span>ATLAS status</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem", color: toneColor(st.tone) }}>
            <span
              className="health-dot"
              style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: toneColor(st.tone) }}
            />
            {st.label}
          </span>
        </div>

        <div className="health-row">
          <span>System posture</span>
          <b style={{ color: postureColorVar }}>{posture}</b>
        </div>

        <div className="health-row">
          <span>Active tasks</span>
          <b>{status ? status.active_task_count : "—"}</b>
        </div>

        <div className="health-row">
          <span>Approvals waiting</span>
          <b className={status && status.pending_approval_count > 0 ? "ember" : ""}>
            {status ? status.pending_approval_count : "—"}
          </b>
        </div>

        <div className="health-row" style={{ borderBottom: "none" }}>
          <span>Environment</span>
          <b>{status ? `${status.environment} · v${status.version}` : "—"}</b>
        </div>
      </aside>
    </section>
  );
}
