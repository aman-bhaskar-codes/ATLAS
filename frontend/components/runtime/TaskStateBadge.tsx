import React from "react";
import { Badge } from "../primitives/Badge";
import type { Task } from "../../lib/api/contracts";

interface TaskStateBadgeProps {
  state: Task["state"];
  className?: string;
}

export function TaskStateBadge({ state, className }: TaskStateBadgeProps) {
  let variant: React.ComponentProps<typeof Badge>["variant"] = "default";
  let label: string = state;

  switch (state) {
    case "completed":
      variant = "success";
      break;
    case "failed":
    case "cancelled":
      variant = "error";
      break;
    case "created":
    case "ready":
      variant = "default";
      break;
    case "building_context":
    case "planning":
    case "reasoning":
    case "waiting_tool":
    case "executing":
    case "observing":
      variant = "info";
      label = state.replace("_", " ");
      break;
  }

  return (
    <Badge variant={variant} className={`capitalize ${className || ""}`}>
      {label}
    </Badge>
  );
}
