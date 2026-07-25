import React from "react";

export function Skeleton({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`animate-pulse bg-[var(--color-ink-800)] rounded ${className}`}
      {...props}
    />
  );
}
