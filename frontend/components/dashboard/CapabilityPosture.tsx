"use client";

import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";
import Link from "next/link";
import { Brain, BookOpen, Mail, Globe2 } from "lucide-react";

export function CapabilityPosture() {
  const { data: capabilities } = useQuery({
    queryKey: ["capabilities"],
    queryFn: atlasApi.capabilities,
    refetchInterval: 15000,
  });

  const activeProviders = capabilities?.filter((c) => c.state === "ready") || [];
  const intelligenceStatus = activeProviders.length > 0 ? "healthy" : "warn";

  return (
    <section className="panel">
      <div className="section-head">
        <h2>Capability posture</h2>
        <Link href="/capabilities">Manage providers</Link>
      </div>
      <div className="cap-grid">
        <div className="cap">
          <div className="cap-head">
            <Brain />
            <span className={`status ${intelligenceStatus === 'warn' ? 'warn' : ''}`}></span>
          </div>
          <b>Intelligence</b>
          <small>{activeProviders.length} providers healthy</small>
          <span className="mono">local-first</span>
        </div>
        <div className="cap">
          <div className="cap-head">
            <BookOpen />
            <span className="status"></span>
          </div>
          <b>Knowledge</b>
          <small>official sources ready</small>
          <span className="mono">free-first</span>
        </div>
        <div className="cap">
          <div className="cap-head">
            <Mail />
            <span className="status warn"></span>
          </div>
          <b>Email</b>
          <small>approval path active</small>
          <span className="mono">gated writes</span>
        </div>
        <div className="cap">
          <div className="cap-head">
            <Globe2 />
            <span className="status off"></span>
          </div>
          <b>Browser</b>
          <small>planned, not enabled</small>
          <span className="mono">phase 6.7</span>
        </div>
      </div>
    </section>
  );
}
