import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "ATLAS | Research",
};

/** Session content inherits the shared Research platform chrome from AppChrome. */
export default function ResearchSessionLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
