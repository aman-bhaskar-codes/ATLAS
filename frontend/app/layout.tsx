import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { Providers } from "./providers";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

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
      <body
        className={`${inter.variable} antialiased bg-[var(--color-ink-950)] text-[var(--color-paper-100)]`}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
