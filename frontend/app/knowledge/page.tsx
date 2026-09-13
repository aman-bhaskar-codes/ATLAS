import { Metadata } from "next";
import { KnowledgeDashboard } from "@/components/knowledge/KnowledgeDashboard";

export const metadata: Metadata = {
  title: "Knowledge Fabric | ATLAS",
};

export default function KnowledgePage() {
  return <KnowledgeDashboard />;
}
