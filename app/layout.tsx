import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "星钥科研智能工作台",
  description: "面向科研论文与技术文档，集成文献引用、智能配图、科学制图与论文写作的本地科研工作台。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
