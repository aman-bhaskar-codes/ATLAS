// frontend/features/runtime-console/reconcile.ts
// Event deduplication and gap detection per Phase Two spec section 5.
// This is the single place where sequence validation logic lives.

import type { TaskEvent } from "../../lib/api/contracts";

export interface ReconcileResult {
  events: TaskEvent[];
  hasGap: boolean;
  lastSequence: number;
}

/**
 * Merge newEvents into existingEvents.
 * - Deduplicates by event_id
 * - Detects sequence gaps (missing sequence numbers)
 * - Returns events sorted by sequence
 */
export function reconcile(
  existing: TaskEvent[],
  incoming: TaskEvent[],
): ReconcileResult {
  const seenIds = new Set(existing.map((e) => e.event_id));
  const merged = [...existing];

  for (const event of incoming) {
    if (!seenIds.has(event.event_id)) {
      seenIds.add(event.event_id);
      merged.push(event);
    }
  }

  // Sort by sequence — source of truth
  merged.sort((a, b) => a.sequence - b.sequence);

  // Gap detection: sequences should be contiguous within a task
  let hasGap = false;
  for (let i = 1; i < merged.length; i++) {
    if (merged[i].sequence !== merged[i - 1].sequence + 1) {
      hasGap = true;
      break;
    }
  }

  const lastSequence = merged.length > 0 ? merged[merged.length - 1].sequence : 0;
  return { events: merged, hasGap, lastSequence };
}
