"use client";

import { useState } from "react";
import { Crosshair, FileText, Image, Mic } from "lucide-react";
import { SettingsScope, TextModelSettings, TranscriptionSettings } from "./ai-settings";
import { useModelResource } from "./model-controls";
import { modelHashes, settingsSections, useSettingsHash } from "./sections";
import ThumbnailAISettings from "./thumbnail-ai-settings";

const tasks = [
  { id: "ranking", label: "Moment detection", Icon: Crosshair,
    description: "Selects moments for speech-based Short Clips and AI Transcript Highlights." },
  { id: "transcription", label: "Transcription", Icon: Mic,
    description: "Runs Whisper on the Cutframe worker for word timings, captions, and transcript analysis." },
  { id: "descriptions", label: "Descriptions", Icon: FileText,
    description: "Writes publication descriptions and relevant hashtags." },
  { id: "thumbnails", label: "Thumbnails", Icon: Image,
    description: "Generates cover artwork for clips and highlight reels." },
];

export default function ModelSettings() {
  const hash = useSettingsHash();
  const active = tasks.find(({ id }) => hash === `#${modelHashes[id]}`)?.id || "ranking";
  const [installationScope, setInstallationScope] = useState(false);
  const workspace = useModelResource("ai-settings/");
  const installation = useModelResource("ai-settings/?scope=installation");
  const descriptions = useModelResource("description-settings/");
  const thumbnails = useModelResource("thumbnail-ai-settings/");
  const ranking = workspace.data;
  const narration = descriptions.data;
  const cover = thumbnails.data;
  const summaries = {
    ranking: ranking?.connection?.model || (ranking ? "Set up a model" : "Workspace selection"),
    transcription: ranking?.settings.whisper_model || ranking?.whisper.model || "Local Whisper",
    descriptions: narration?.connection?.model || narration?.settings.model ||
      (narration && !narration.settings.provider ? ranking?.connection?.model : "") || "Set up a model",
    thumbnails: cover?.settings.provider ? cover.settings.model || "Set up a model" : "Frame covers",
  };

  function refreshConnections(shared) {
    if (shared) workspace.reload();
    descriptions.reload();
    thumbnails.reload();
  }

  function handleTabKeys(event, index) {
    if (event.key === " ") {
      event.preventDefault();
      event.currentTarget.click();
      return;
    }
    const offsets = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 };
    let next;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tasks.length - 1;
    else if (offsets[event.key]) next = (index + offsets[event.key] + tasks.length) % tasks.length;
    else return;
    event.preventDefault();
    const tab = document.getElementById(`model-tab-${tasks[next].id}`);
    tab.focus();
    tab.click();
  }

  return (
    <section className="model-workspace" aria-labelledby={settingsSections.models.id}>
      <header className="model-workspace-heading">
        <span className="heading-icon"><settingsSections.models.Icon size={22} aria-hidden="true" /></span>
        <div><h2 id={settingsSections.models.id} tabIndex={-1}>AI models</h2>
          <p>See your workspace models and configure each task.</p></div>
      </header>
      <div className="model-tabs" role="tablist" aria-label="AI model tasks">
        {tasks.map(({ id, label, Icon }, index) => (
          <a key={id} className="model-tab" role="tab" id={`model-tab-${id}`}
            href={`#${modelHashes[id]}`} aria-selected={id === active} aria-controls={`model-panel-${id}`}
            tabIndex={id === active ? 0 : -1} aria-label={`${label}, ${summaries[id]}`}
            onKeyDown={(event) => handleTabKeys(event, index)}>
            <Icon size={18} aria-hidden="true" />
            <span><strong>{label}</strong><small title={summaries[id]}>{summaries[id]}</small></span>
          </a>
        ))}
      </div>
      {tasks.map(({ id, label, description }) => (
        <section className="panel settings-panel model-task" key={id} id={`model-panel-${id}`}
          role="tabpanel" aria-labelledby={`model-tab-${id}`} hidden={active !== id}>
          <div className="model-task-heading">
            <div><h3>{label}</h3><p>{description}</p></div>
            {(id === "ranking" || id === "transcription") &&
              <SettingsScope installation={installationScope} onChange={setInstallationScope} />}
          </div>
          {id === "ranking" && <>
            <div hidden={installationScope}>
              <TextModelSettings resource={workspace} onSave={() => refreshConnections(false)} />
            </div>
            <div hidden={!installationScope}>
              <TextModelSettings resource={installation} installation onSave={() => refreshConnections(true)} />
            </div>
          </>}
          {id === "transcription" && <>
            <div hidden={installationScope}><TranscriptionSettings resource={workspace} /></div>
            <div hidden={!installationScope}>
              <TranscriptionSettings resource={installation} installation onSave={workspace.reload} />
            </div>
          </>}
          {id === "descriptions" && <TextModelSettings resource={descriptions} descriptions ranking={ranking} />}
          {id === "thumbnails" && <ThumbnailAISettings resource={thumbnails} />}
        </section>
      ))}
    </section>
  );
}
