"use client";

import { useSyncExternalStore } from "react";
import { Cpu, FileText, HardDrive, Image, Link2, Monitor, SlidersHorizontal } from "lucide-react";

export const settingsSections = {
  render: { id: "render-defaults-title", label: "Render defaults", Icon: SlidersHorizontal },
  models: { id: "ai-settings-title", label: "AI models", Icon: Cpu },
  descriptions: { id: "description-settings-title", label: "Descriptions", Icon: FileText },
  thumbnails: { id: "thumbnail-ai-settings-title", label: "Thumbnails", Icon: Image },
  publishing: { id: "publishing-settings-title", label: "Publishing", Icon: Link2 },
  storage: { id: "storage-settings-title", label: "Storage", Icon: HardDrive },
  appearance: { id: "appearance-title", label: "Appearance", Icon: Monitor },
};

function subscribe(listener) {
  window.addEventListener("hashchange", listener);
  window.addEventListener("popstate", listener);
  return () => {
    window.removeEventListener("hashchange", listener);
    window.removeEventListener("popstate", listener);
  };
}

export function useSettingsSection() {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash, () => "");
  return Object.values(settingsSections).find(({ id }) => hash === `#${id}`) ?? settingsSections.render;
}
