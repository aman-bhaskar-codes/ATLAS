import type { Metadata } from "next";
import { Providers } from "./providers";
import { AppChrome } from "@/components/layout/AppChrome";
import "./globals.css";

export const metadata: Metadata = {
  title: "ATLAS | Command Center",
  description: "Local-first, safety-governed autonomous task runtime.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body suppressHydrationWarning>
        <Providers>
          <AppChrome>{children}</AppChrome>
        </Providers>
      </body>
    </html>
  );
}
