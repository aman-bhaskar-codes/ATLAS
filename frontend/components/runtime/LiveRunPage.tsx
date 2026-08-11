"use client";
import { Badge } from "../primitives/Badge";
import { Panel } from "../primitives/Panel";
import { ConnectionState, elapsedSeconds, isTerminal, Task, TaskEvent, CancelTaskResponse } from "../../lib/api/contracts";
import { formatDistanceToNowStrict } from "date-fns";

export function ConnectionBadge({
  state,
}: {
  state: ConnectionState;
}) {
  if (state === "connected") {
    return (
      <Badge variant="success" className="animate-pulse">
        Live
      </Badge>
    );
  }
  if (state === "reconnecting") {
    return <Badge variant="warning">Reconnecting...</Badge>;
  }
  if (state === "stale" || state === "offline") {
    return (
      <Badge variant="error">
        {state === "stale" ? "Stale Data" : "Offline"}
      </Badge>
    );
  }
  return null;
}

import { Brain, Wrench, Eye, CheckCircle2, XCircle, ShieldAlert, CircleDot } from "lucide-react";

export function TimelineEventRow({ event }: { event: TaskEvent }) {
  const timeStr = formatDistanceToNowStrict(new Date(event.ts), { addSuffix: true });
  
  let Icon = CircleDot;
  let colorVar = 'var(--paper-500)';
  
  if (event.state === 'reasoning' || event.state === 'planning') {
    Icon = Brain;
    colorVar = 'var(--indigo-400)';
  } else if (event.state === 'executing' || event.state === 'waiting_tool') {
    Icon = Wrench;
    colorVar = 'var(--gold-400)';
  } else if (event.state === 'observing') {
    Icon = Eye;
    colorVar = 'var(--cyan-400)';
  } else if (event.state === 'completed') {
    Icon = CheckCircle2;
    colorVar = 'var(--jade-400)';
  } else if (event.state === 'failed') {
    Icon = XCircle;
    colorVar = 'var(--danger-400)';
  } else if (event.event_type === 'safety_gate' || event.requires_approval) {
    Icon = ShieldAlert;
    colorVar = 'var(--ember-400)';
  }

  return (
    <div style={{ position: 'relative', paddingLeft: '2.5rem', paddingBottom: '1.5rem', borderLeft: '1px solid var(--line)' }}>
      <div style={{ position: 'absolute', left: '-13px', top: '2px', display: 'flex', alignItems: 'center', justifyContent: 'center', width: '26px', height: '26px', borderRadius: '50%', backgroundColor: 'var(--ink-950)', border: `1px solid ${colorVar}` }}>
        <Icon style={{ width: '12px', height: '12px', color: colorVar }} />
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontSize: '0.65rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: colorVar, fontWeight: 600 }}>{event.state}</span>
          <span style={{ fontSize: '0.75rem', color: 'var(--paper-500)' }}>{timeStr}</span>
        </div>
        <div style={{ fontWeight: 500, color: 'var(--paper-100)', marginTop: '0.25rem' }}>{event.summary}</div>
        
        {/* Safe Metadata Drawer */}
        {(event.capability || Object.keys(event.safe_metadata).length > 0) && (
          <div style={{ marginTop: '0.5rem', display: 'flex', flexDirection: 'column', gap: '0.25rem', borderRadius: '4px', background: 'var(--ink-850)', padding: '0.75rem', fontSize: '0.85rem', color: 'var(--paper-300)' }}>
            {event.capability && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span style={{ color: 'var(--paper-500)' }}>capability:</span>
                <span style={{ color: 'var(--gold-400)' }}>{event.capability}</span>
                {event.operation && <span style={{ color: 'var(--paper-300)' }}>.{event.operation}</span>}
              </div>
            )}
            {Object.entries(event.safe_metadata).map(([k, v]) => (
              <div key={k} style={{ display: 'flex', gap: '0.5rem' }}>
                <span style={{ color: 'var(--paper-500)' }}>{k}:</span>
                <span style={{ wordBreak: 'break-all' }}>{v}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function RuntimeHeader({
  task,
  onCancel,
  isCancelling,
  cancelResult,
}: {
  task: Task;
  onCancel: () => void;
  isCancelling: boolean;
  cancelResult: CancelTaskResponse | undefined;
}) {
  const elapsed = elapsedSeconds(task);
  const terminal = isTerminal(task.state);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', borderBottom: '1px solid var(--line)', paddingBottom: '1.5rem' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ fontSize: '1.25rem', fontWeight: 500, color: 'var(--paper-100)', margin: 0 }}>Task {task.id.slice(-8)}</h1>
          <p style={{ marginTop: '0.25rem', fontSize: '0.85rem', color: 'var(--paper-500)', margin: 0 }}>Source: {task.source}</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <Badge variant={terminal ? (task.state === "completed" ? "success" : "error") : "warning"}>
            {task.state}
          </Badge>
          {!terminal && (
            <button
              onClick={onCancel}
              disabled={isCancelling}
              className="ghost-btn kill"
              style={{ padding: '4px 12px', fontSize: '0.85rem' }}
            >
              {isCancelling ? "Cancelling..." : "Cancel"}
            </button>
          )}
        </div>
      </div>
      <div className="panel" style={{ padding: '1rem', background: 'var(--ink-850)', borderTop: 'none', borderRadius: '4px' }}>
        <p className="mono" style={{ fontSize: '0.85rem', color: 'var(--paper-300)', margin: 0 }}>{task.request}</p>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', color: 'var(--paper-500)' }}>
        <div>
          Elapsed: {elapsed}s · Created: {new Date(task.created_at).toLocaleTimeString()}
        </div>
        <div className="mono">
          Tokens: ~4200 · Cost: $0.018
        </div>
      </div>
      {cancelResult && !cancelResult.accepted && (
        <div style={{ fontSize: '0.85rem', color: 'var(--danger-400)' }}>{cancelResult.message}</div>
      )}
    </div>
  );
}
