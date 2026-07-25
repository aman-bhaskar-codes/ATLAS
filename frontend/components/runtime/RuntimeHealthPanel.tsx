import React from "react";
import type { RuntimeHealth, RuntimeStatus } from "../../lib/api/contracts";
import { Panel } from "../primitives/Panel";
import { Badge } from "../primitives/Badge";
import { CheckCircle2, AlertTriangle, XCircle, Power, Clock } from "lucide-react";

interface RuntimeHealthPanelProps {
  status?: RuntimeStatus;
  health?: RuntimeHealth;
  isLoading: boolean;
  error?: string | Error;
}

export function RuntimeHealthPanel({ status, health, isLoading, error }: RuntimeHealthPanelProps) {
  if (error) {
    return (
      <Panel title="System Posture" className="border-[var(--color-danger-400)]">
        <div className="flex items-center gap-2 text-[var(--color-danger-400)]">
          <XCircle className="w-5 h-5" />
          <span className="font-medium">Backend Unavailable</span>
        </div>
      </Panel>
    );
  }

  if (isLoading || !status || !health) {
    return (
      <Panel title="System Posture">
        <div className="animate-pulse flex gap-4">
          <div className="h-10 w-24 bg-[var(--color-ink-800)] rounded"></div>
          <div className="h-10 w-32 bg-[var(--color-ink-800)] rounded"></div>
        </div>
      </Panel>
    );
  }

  const getOverallIcon = () => {
    if (status.kill_switch_active) return <Power className="w-5 h-5 text-[var(--color-danger-400)]" />;
    if (health.overall === "healthy") return <CheckCircle2 className="w-5 h-5 text-[var(--color-jade-400)]" />;
    if (health.overall === "degraded") return <AlertTriangle className="w-5 h-5 text-[var(--color-gold-400)]" />;
    return <XCircle className="w-5 h-5 text-[var(--color-danger-400)]" />;
  };

  return (
    <Panel title="System Posture">
      <div className="flex flex-wrap gap-6 items-center">
        
        {/* Core Status */}
        <div className="flex items-center gap-3 border-r border-[var(--color-line)] pr-6">
          {getOverallIcon()}
          <div>
            <div className="text-sm font-medium text-[var(--color-paper-100)] flex items-center gap-2">
              ATLAS Runtime
              {status.kill_switch_active && <Badge variant="error">KILL SWITCH ACTIVE</Badge>}
            </div>
            <div className="text-xs text-[var(--color-paper-300)] mt-0.5">
              v{status.version} · {status.environment}
            </div>
          </div>
        </div>

        {/* Workload */}
        <div className="flex items-center gap-4">
          <div>
            <div className="text-2xl font-light text-[var(--color-paper-100)]">{status.active_task_count}</div>
            <div className="text-xs text-[var(--color-paper-300)] font-medium">ACTIVE TASKS</div>
          </div>
          <div>
            <div className="text-2xl font-light text-[var(--color-paper-100)]">{status.pending_approval_count}</div>
            <div className="text-xs text-[var(--color-paper-300)] font-medium">PENDING APPROVALS</div>
          </div>
        </div>

        {/* Health Checks */}
        <div className="flex-1 flex flex-col items-end">
          <div className="flex items-center gap-2 text-xs text-[var(--color-paper-300)] mb-1">
            <Clock className="w-3 h-3" />
            Last audit: {status.last_audit_at ? new Date(status.last_audit_at).toLocaleTimeString() : 'Never'}
          </div>
          <div className="flex gap-2">
            {health.checks.map(check => (
              <Badge 
                key={check.name} 
                variant={check.status === "pass" ? "success" : check.status === "warn" ? "warning" : "error"}
              >
                {check.name}
              </Badge>
            ))}
          </div>
        </div>

      </div>
    </Panel>
  );
}
