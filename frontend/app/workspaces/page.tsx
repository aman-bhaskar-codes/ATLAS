import { Metadata } from "next";
import { WorkspacesDashboard } from "@/components/workspace/WorkspacesDashboard";

export const metadata: Metadata = {
  title: "IDE | ATLAS",
};

export default function WorkspacesPage() {
  return <><h1>IDE workspaces</h1><p className="platform-description">Select a workspace or open a project to start the development workbench.</p><WorkspacesDashboard /></>;
}
