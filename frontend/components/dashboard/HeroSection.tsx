"use client";

import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";

export function HeroSection() {
  const { data: health, isLoading: healthLoading, isError: healthError } = useQuery({
    queryKey: ["runtimeHealth"],
    queryFn: atlasApi.runtimeHealth,
    refetchInterval: 15000,
  });

  const { data: approvals } = useQuery({
    queryKey: ["approvals"],
    queryFn: atlasApi.approvals,
    refetchInterval: 15000,
  });

  const isHealthy = healthError ? false : health?.overall === "healthy";
  const numApprovals = approvals?.length || 0;
  
  let statusText = "connecting...";
  if (healthError) statusText = "disconnected";
  else if (health) statusText = health.overall;

  let runtimeText = "booting";
  if (healthError) runtimeText = "offline";
  else if (health) runtimeText = "idle"; // TODO: wire active task if running

  const dateStr = new Intl.DateTimeFormat('en-GB', { 
    weekday: 'long', 
    day: 'numeric', 
    month: 'long', 
    year: 'numeric' 
  }).format(new Date());

  // Mock spend progress
  const spend = 0.18;
  const budget = 1.00;
  const spendPercent = Math.min(100, (spend / budget) * 100);

  return (
    <section className="hero">
      <div>
        <div className="eyebrow">{dateStr} · local-first</div>
        <h1 className="display">Good morning, Aman.</h1>
        <p>ATLAS is online, quiet, and ready. Ask for something. It will show its plan before it acts.</p>
      </div>
      <aside className="health">
        <div className="health-head">
          <span>System posture</span>
          <span className={isHealthy ? "jade" : "ember"}>
            <span className="health-dot" style={{ display: 'inline-block', marginRight: '8px', backgroundColor: isHealthy ? 'var(--jade-400)' : 'var(--ember-400)' }}></span>
            {statusText}
          </span>
        </div>
        <div className="health-row">
          <span>Runtime</span>
          <b>{runtimeText}</b>
        </div>
        <div className="health-row">
          <span>Primary model</span>
          <b>{healthError ? "—" : "GLM-5.2 (Remote)"}</b>
        </div>
        <div className="health-row">
          <span>Approvals</span>
          <b className={numApprovals > 0 ? "ember" : ""}>
            {numApprovals} waiting
          </b>
        </div>
        <div className="health-row" style={{ flexDirection: 'column', gap: '8px', borderBottom: 'none' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
            <span>Spend today</span>
            <b>${spend.toFixed(2)} / ${budget.toFixed(2)}</b>
          </div>
          <div style={{ width: '100%', height: '4px', background: 'var(--ink-950)', borderRadius: '2px', overflow: 'hidden' }}>
            <div style={{ width: `${spendPercent}%`, height: '100%', background: spendPercent > 80 ? 'var(--ember-400)' : 'var(--gold-400)' }}></div>
          </div>
        </div>
      </aside>
    </section>
  );
}
