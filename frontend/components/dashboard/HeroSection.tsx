"use client";

import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";

export function HeroSection() {
  const { data: health } = useQuery({
    queryKey: ["runtimeHealth"],
    queryFn: atlasApi.runtimeHealth,
    refetchInterval: 15000,
  });

  const { data: approvals } = useQuery({
    queryKey: ["approvals"],
    queryFn: atlasApi.approvals,
    refetchInterval: 15000,
  });

  const isHealthy = health?.overall === "healthy";
  const numApprovals = approvals?.length || 0;

  const dateStr = new Intl.DateTimeFormat('en-GB', { 
    weekday: 'long', 
    day: 'numeric', 
    month: 'long', 
    year: 'numeric' 
  }).format(new Date());

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
            {health?.overall || "connecting..."}
          </span>
        </div>
        <div className="health-row">
          <span>Runtime</span>
          <b>{health ? "ready" : "booting"}</b>
        </div>
        <div className="health-row">
          <span>Primary model</span>
          <b>{health ? "GLM-5.2 (Remote)" : "—"}</b>
        </div>
        <div className="health-row">
          <span>Approvals</span>
          <b className={numApprovals > 0 ? "ember" : ""}>
            {numApprovals} waiting
          </b>
        </div>
        <div className="health-row">
          <span>Spend today</span>
          <b>$0.18 / $1.00</b>
        </div>
      </aside>
    </section>
  );
}
