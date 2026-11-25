"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Clapperboard, History, Plus, Scissors, Settings } from "lucide-react";

export default function Shell({ children }) {
  const pathname = usePathname();
  const onStudio = pathname === "/";
  const onSettings = pathname === "/settings";
  const onRender = pathname.startsWith("/jobs/");
  return (
    <div className="shell">
      <aside className="sidebar">
        <Link href="/" className="brand" aria-label="Cutframe Studio home">
          <span className="brand-mark"><Scissors size={21} strokeWidth={2.3} aria-hidden="true" /></span>
          <span>Cutframe<span className="brand-dot">.</span></span>
        </Link>
        <nav className="navigation" aria-label="Workspace">
          <Link href="/" className={onStudio ? "nav-item current" : "nav-item"} aria-current={onStudio ? "page" : undefined}>
            <Plus size={19} aria-hidden="true" /><span>New render</span>
          </Link>
          <Link href="/#history" className={onRender ? "nav-item current" : "nav-item"}>
            <History size={18} aria-hidden="true" /><span>Render history</span>
          </Link>
          <Link href="/settings" className={onSettings ? "nav-item current" : "nav-item"} aria-current={onSettings ? "page" : undefined}>
            <Settings size={18} aria-hidden="true" /><span>Settings</span>
          </Link>
        </nav>
        <div className="sidebar-footer"><Clapperboard size={17} aria-hidden="true" /> Local workspace</div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span>{onStudio ? "Studio" : onSettings ? "Settings" : "Render details"}</span>
          <span className="topbar-brand">Cutframe Studio</span>
        </header>
        <main id="main-content" className="content">{children}</main>
      </div>
    </div>
  );
}
