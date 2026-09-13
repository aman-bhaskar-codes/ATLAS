import { Metadata } from "next";
import { WorkspacesDashboard } from "@/components/workspace/WorkspacesDashboard";

export const metadata: Metadata = {
  title: "Workspaces | ATLAS",
};

export default function WorkspacesPage() {
  return <WorkspacesDashboard />;
}
