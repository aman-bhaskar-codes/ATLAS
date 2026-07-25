import React from "react";
import { Loader2 } from "lucide-react";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md" | "lg";
  isLoading?: boolean;
}

export function Button({
  className = "",
  variant = "primary",
  size = "md",
  isLoading = false,
  children,
  disabled,
  ...props
}: ButtonProps) {
  const base = "inline-flex items-center justify-center font-medium rounded-[var(--radius-sm)] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-ink-950)] focus-visible:ring-[var(--color-gold-400)] disabled:opacity-50 disabled:cursor-not-allowed";
  
  const variants = {
    primary: "bg-[var(--color-royal-500)] text-[var(--color-paper-100)] hover:bg-[var(--color-royal-400)]",
    secondary: "bg-[var(--color-ink-800)] text-[var(--color-paper-100)] hover:bg-[var(--color-ink-700)] border border-[var(--color-line)]",
    danger: "bg-[var(--color-danger-400)] text-white hover:bg-opacity-80",
    ghost: "bg-transparent text-[var(--color-paper-300)] hover:bg-[var(--color-ink-800)] hover:text-[var(--color-paper-100)]",
  };

  const sizes = {
    sm: "px-2 py-1 text-xs",
    md: "px-4 py-2 text-sm",
    lg: "px-6 py-3 text-base",
  };

  return (
    <button
      className={`${base} ${variants[variant]} ${sizes[size]} ${className}`}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}
