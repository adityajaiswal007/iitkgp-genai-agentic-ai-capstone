import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Operations Copilot",
  description:
    "A focused workspace for conversations, research, and everyday operations.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
