import type { Metadata } from "next";

import { Shell } from "@/components/shell";
import { ToastProvider } from "@/components/ui/toast";

import "./globals.css";

export const metadata: Metadata = {
  title: "VAIIP — Vianomics Trader OS",
  description:
    "Vianomics AI Investment Intelligence Platform — institutional investment workstation",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ToastProvider>
          <Shell>{children}</Shell>
        </ToastProvider>
      </body>
    </html>
  );
}
