import React from 'react';

export function DecisionBadge({ tier }: { tier: number }) {
  return (
    <span className="text-[0.67rem] tracking-[0.08em] uppercase px-[7px] py-[4px] rounded-full bg-[oklch(70%_0.16_35/0.14)] text-[var(--ember-400)] whitespace-nowrap">
      Tier {tier}
    </span>
  );
}
