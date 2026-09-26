"use client";

import { use } from "react";
import { Workbench } from "@/components/workbench/Workbench";

/**
 * `/dev/[workspaceId]` — the Development Workbench entry point (spec §1).
 * Opened in a separate window from the Command Center. The dynamic segment is the
 * backend `workspace_id`; the workbench resolves it against the durable workspace
 * list, so the URL alone rehydrates the full session after a close/reopen.
 */
export default function DevWorkspacePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = use(params);
  return <Workbench workspaceId={decodeURIComponent(workspaceId)} />;
}
