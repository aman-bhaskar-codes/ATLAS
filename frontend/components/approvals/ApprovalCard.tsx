import React from "react";
import type { Approval } from "../../lib/api/contracts";
import { Panel } from "../primitives/Panel";
import { Badge } from "../primitives/Badge";
import { Button } from "../primitives/Button";
import { AlertOctagon, Check, X, ShieldAlert } from "lucide-react";

interface ApprovalCardProps {
  approval: Approval;
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
  isLoading: boolean;
}

export function ApprovalCard({ approval, onApprove, onDeny, isLoading }: ApprovalCardProps) {
  const isExpired = new Date(approval.expires_at) < new Date();

  return (
    <Panel className={`border-[var(--color-gold-500)] ${isExpired ? 'opacity-50' : ''}`}>
      <div className="flex items-start gap-4">
        <div className="mt-1 text-[var(--color-gold-400)]">
          {approval.tier > 2 ? <ShieldAlert className="w-6 h-6" /> : <AlertOctagon className="w-6 h-6" />}
        </div>
        
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-4 mb-2">
            <div className="flex items-center gap-3">
              <h4 className="text-sm font-medium text-[var(--color-paper-100)]">Action Required</h4>
              <Badge variant="warning">Tier {approval.tier}</Badge>
            </div>
            <div className="text-xs text-[var(--color-paper-500)]">
              Expires: {new Date(approval.expires_at).toLocaleTimeString()}
            </div>
          </div>
          
          <p className="text-base text-[var(--color-paper-100)] mb-3">
            {approval.prompt}
          </p>

          <div className="bg-[var(--color-ink-950)] border border-[var(--color-line)] rounded p-3 mb-4 text-xs font-mono text-[var(--color-paper-300)] overflow-x-auto">
            <div className="mb-2 pb-2 border-b border-[var(--color-line)]">
              <span className="text-[var(--color-paper-500)]">Capability: </span>
              <span className="text-[var(--color-royal-400)]">{approval.capability}</span>
              <span className="text-[var(--color-paper-500)] ml-4">Operation: </span>
              <span className="text-[var(--color-jade-400)]">{approval.operation}</span>
            </div>
            <pre className="whitespace-pre-wrap">{approval.preview}</pre>
          </div>

          {approval.warnings.length > 0 && (
            <div className="mb-4 space-y-1 text-sm text-[var(--color-gold-400)]">
              {approval.warnings.map((warning, i) => (
                <div key={i} className="flex items-start gap-2">
                  <AlertOctagon className="w-4 h-4 shrink-0 mt-0.5" />
                  <span>{warning}</span>
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center gap-3 mt-4">
            <Button 
              variant="primary" 
              className="bg-[var(--color-gold-500)] hover:bg-[var(--color-gold-400)] text-[var(--color-ink-950)]"
              onClick={() => onApprove(approval.id)}
              isLoading={isLoading}
              disabled={isExpired}
            >
              <Check className="w-4 h-4 mr-2" />
              Approve
            </Button>
            <Button 
              variant="secondary"
              onClick={() => onDeny(approval.id)}
              isLoading={isLoading}
              disabled={isExpired}
            >
              <X className="w-4 h-4 mr-2" />
              Deny
            </Button>
          </div>
        </div>
      </div>
    </Panel>
  );
}
