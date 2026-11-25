"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ChevronDown, Clapperboard, History, Plus, Scissors, Settings } from "lucide-react";
import { settingsSections, useSettingsSection } from "./settings/sections";

export default function Shell({ children }) {
  const pathname = usePathname();
  const onStudio = pathname === "/";
  const onSettings = pathname === "/settings";
  const onRender = pathname.startsWith("/jobs/");
  const section = useSettingsSection();
  const [settingsOpen, setSettingsOpen] = useState(null);
  const showSettings = settingsOpen ?? onSettings;
  const SettingsLink = onSettings ? "a" : Link;
  return (
    <div className="shell" data-settings-open={showSettings}>
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
          <div className={onSettings ? "nav-settings current" : "nav-settings"}>
            <SettingsLink href={onSettings ? `#${settingsSections.render.id}` : "/settings"}
              className="nav-item" aria-current={onSettings ? "page" : undefined} onClick={() => setSettingsOpen(true)}>
              <Settings size={18} aria-hidden="true" /><span>Settings</span>
            </SettingsLink>
            <button type="button" className="nav-disclosure" aria-expanded={showSettings} aria-controls="settings-submenu"
              aria-label={showSettings ? "Collapse settings menu" : "Expand settings menu"}
              onClick={() => setSettingsOpen(!showSettings)}>
              <ChevronDown size={16} aria-hidden="true" />
            </button>
          </div>
          <ul className="nav-submenu" id="settings-submenu" hidden={!showSettings}>
            {Object.values(settingsSections).map(({ id, label, Icon }) => (
              <li key={id}>
                <SettingsLink href={onSettings ? `#${id}` : `/settings#${id}`}
                  aria-current={onSettings && section.id === id ? "location" : undefined}>
                  <Icon size={15} aria-hidden="true" /><span>{label}</span>
                </SettingsLink>
              </li>
            ))}
          </ul>
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
