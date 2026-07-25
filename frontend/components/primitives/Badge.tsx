import React from "react";

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "success" | "warning" | "error" | "info";
}

export function Badge({ className = "", variant = "default", children, ...props }: BadgeProps) {
  const base = "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border";
  
  const variants = {
    default: "bg-[var(--color-ink-800)] text-[var(--color-paper-300)] border-[var(--color-line)]",
    success: "bg-emerald-950 text-[var(--color-jade-400)] border-emerald-900",
    warning: "bg-amber-950 text-[var(--color-gold-400)] border-amber-900",
    error: "bg-red-950 text-[var(--color-danger-400)] border-red-900",
    info: "bg-indigo-950 text-[var(--color-royal-400)] border-indigo-900",
  };

  return (
    <span className={`${base} ${variants[variant]} ${className}`} {...props}>
      {children}
    </span>
  );
}
