"use client";

import { Send, HeartPulse, Power } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";

export function Topbar() {
  const { data: health } = useQuery({
    queryKey: ["runtimeHealth"],
    queryFn: atlasApi.runtimeHealth,
    refetchInterval: 15000,
  });

  return (
    <header className="topbar">
      <div className="crumb">
        ATLAS / <strong>Command Center</strong>
      </div>
      <div className="top-actions">
        <button 
          className="ghost-btn" 
          onClick={() => alert("Telegram bridge connection is managed by the backend configuration.")}
        >
          <Send width={18} height={18} />
          Telegram bridge <span className="jade">connected</span>
        </button>
        <button 
          className="icon-btn" 
          aria-label="Open system health"
          onClick={() => alert(`Runtime status: ${health?.overall || 'unknown'}`)}
        >
          <HeartPulse width={18} height={18} />
        </button>
        <button 
          className="icon-btn kill" 
          aria-label="Kill switch"
          onClick={() => {
            if (window.confirm("Trip the ATLAS kill switch? New actions will halt.")) {
              alert("Kill switch request sent. Backend confirmation required.");
            }
          }}
        >
          <Power width={18} height={18} />
        </button>
      </div>
    </header>
  );
}
