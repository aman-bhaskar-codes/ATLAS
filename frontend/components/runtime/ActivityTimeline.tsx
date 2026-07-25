import React from "react";
import type { TaskEvent } from "../../lib/api/contracts";
import { CheckCircle2, Circle, Clock, Info } from "lucide-react";

interface ActivityTimelineProps {
  events: TaskEvent[];
}

export function ActivityTimeline({ events }: ActivityTimelineProps) {
  if (events.length === 0) {
    return <div className="text-sm text-[var(--color-paper-300)] italic">No events yet.</div>;
  }

  return (
    <div className="space-y-4">
      {events.map((event, index) => {
        const isLast = index === events.length - 1;
        
        let Icon = Circle;
        let iconColor = "text-[var(--color-paper-300)]";
        
        if (event.event_type === "completed") {
          Icon = CheckCircle2;
          iconColor = "text-[var(--color-jade-400)]";
        } else if (event.event_type === "failed") {
          Icon = Info;
          iconColor = "text-[var(--color-danger-400)]";
        }

        return (
          <div key={event.event_id} className="relative flex gap-4">
            {!isLast && (
              <div className="absolute left-2.5 top-6 bottom-[-16px] w-px bg-[var(--color-line)]" />
            )}
            
            <div className={`relative z-10 flex items-center justify-center w-5 h-5 mt-0.5 bg-[var(--color-ink-900)] ${iconColor}`}>
              <Icon className="w-4 h-4" />
            </div>
            
            <div className="flex-1 pb-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-[var(--color-paper-100)]">
                  {event.capability || "System"}
                </span>
                <span className="text-xs text-[var(--color-paper-500)] flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {new Date(event.ts).toLocaleTimeString()}
                </span>
              </div>
              <p className="text-sm text-[var(--color-paper-300)] mt-1 whitespace-pre-wrap font-mono text-xs">
                {event.summary}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
