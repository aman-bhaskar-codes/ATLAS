import type { Metadata } from "next";
import { Providers } from "./providers";
import { Sidebar } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { MobileNav } from "@/components/layout/MobileNav";
import "./globals.css";

export const metadata: Metadata = {
  title: "ATLAS | Command Center",
  description: "Elite AI Engineering Ecosystem Control Plane",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body>
        <Providers>
          <div className="app">
            <Sidebar />
            <main className="main">
              <Topbar />
              {children}
            </main>
          </div>
          <MobileNav />
        </Providers>
      </body>
    </html>
  );
}
