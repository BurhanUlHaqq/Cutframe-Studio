"use client";

import { useSyncExternalStore } from "react";
import { Cpu, HardDrive, Link2, Monitor, SlidersHorizontal } from "lucide-react";

export const settingsSections = {
  render: { id: "render-defaults-title", label: "Render defaults", Icon: SlidersHorizontal },
  models: { id: "ai-settings-title", label: "AI models", Icon: Cpu },
  publishing: { id: "publishing-settings-title", label: "Publishing", Icon: Link2 },
  storage: { id: "storage-settings-title", label: "Storage", Icon: HardDrive },
  appearance: { id: "appearance-title", label: "Appearance", Icon: Monitor },
};

export const modelHashes = {
  ranking: settingsSections.models.id,
  transcription: "transcription-settings-title",
  descriptions: "description-settings-title",
  thumbnails: "thumbnail-ai-settings-title",
};

function subscribe(listener) {
  window.addEventListener("hashchange", listener);
  window.addEventListener("popstate", listener);
  return () => {
    window.removeEventListener("hashchange", listener);
    window.removeEventListener("popstate", listener);
  };
}

export function useSettingsHash() {
  return useSyncExternalStore(subscribe, () => window.location.hash, () => "");
}

export function useSettingsSection() {
  const hash = useSettingsHash();
  if (Object.values(modelHashes).includes(hash.slice(1))) return settingsSections.models;
  return Object.values(settingsSections).find(({ id }) => hash === `#${id}`) ?? settingsSections.render;
}
