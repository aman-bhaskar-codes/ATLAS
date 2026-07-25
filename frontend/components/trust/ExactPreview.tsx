import React from 'react';

interface ExactPreviewProps {
  capability: string;
  operation: string;
  destination?: string;
  policy: string;
  expiresIn?: string;
  bodyPreview: React.ReactNode;
}

export function ExactPreview({ capability, operation, destination, policy, expiresIn, bodyPreview }: ExactPreviewProps) {
  return (
    <div className="bg-[var(--ink-850)] border border-[var(--line)] p-4 rounded-lg">
      <h3 className="text-base m-0 mb-3 font-medium">Exact Preview: {operation}</h3>
      <div className="flex justify-between gap-3 border-t border-[var(--line)] py-2 text-[0.8rem]">
        <span className="text-[var(--paper-500)]">Capability</span>
        <b className="font-medium text-right">{capability}</b>
      </div>
      {destination && (
        <div className="flex justify-between gap-3 border-t border-[var(--line)] py-2 text-[0.8rem]">
          <span className="text-[var(--paper-500)]">Destination</span>
          <b className="font-medium text-right">{destination}</b>
        </div>
      )}
      <div className="flex justify-between gap-3 border-t border-[var(--line)] py-2 text-[0.8rem]">
        <span className="text-[var(--paper-500)]">Policy</span>
        <b className="font-medium text-right">{policy}</b>
      </div>
      {expiresIn && (
        <div className="flex justify-between gap-3 border-t border-[var(--line)] py-2 text-[0.8rem]">
          <span className="text-[var(--paper-500)]">Expires</span>
          <b className="text-[var(--ember-400)] font-medium text-right">{expiresIn}</b>
        </div>
      )}
      <div className="border-t border-[var(--line)] pt-3 mt-1 text-[var(--paper-300)] text-[0.83rem] whitespace-pre-wrap font-mono">
        {bodyPreview}
      </div>
    </div>
  );
}
