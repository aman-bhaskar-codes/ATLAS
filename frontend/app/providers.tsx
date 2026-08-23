"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { shouldRetry } from "@/lib/api/retry";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5000,
        refetchOnWindowFocus: true,
        // Without this, react-query's default retried EVERY failure three times —
        // including 404s and schema mismatches, which cannot succeed on a retry.
        // See lib/api/retry.ts for the rule per status.
        retry: shouldRetry,
      },
      mutations: {
        // Mutations are POSTs that create tasks and decide approvals. Retrying one
        // automatically risks doing the thing twice, so the default (no retry)
        // stands — it is stated here so it reads as a decision, not an omission.
        // Idempotency keys make a *user-initiated* retry safe; a silent one still
        // hides the failure.
        retry: false,
      },
    },
  }));

  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
}
