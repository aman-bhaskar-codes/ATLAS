import React from "react";
import { Badge } from "../primitives/Badge";
import { Wifi, WifiOff, Loader2 } from "lucide-react";

interface EventConnectionBadgeProps {
  status: "connected" | "reconnecting" | "closed";
}

export function EventConnectionBadge({ status }: EventConnectionBadgeProps) {
  if (status === "connected") {
    return (
      <Badge variant="success" className="gap-1.5">
        <Wifi className="w-3 h-3" /> Live
      </Badge>
    );
  }
  
  if (status === "reconnecting") {
    return (
      <Badge variant="warning" className="gap-1.5">
        <Loader2 className="w-3 h-3 animate-spin" /> Reconnecting
      </Badge>
    );
  }
  
  return (
    <Badge variant="default" className="gap-1.5">
      <WifiOff className="w-3 h-3" /> Offline
    </Badge>
  );
}
