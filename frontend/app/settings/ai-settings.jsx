"use client";

import { useId, useState } from "react";
import { RefreshCw } from "lucide-react";
import { SelectField } from "../render-options";
import { ModelActions, ModelLoadState, ModelRecommendations, SavedModel, useModelDraft } from "./model-controls";

const ollamaUrl = "http://host.docker.internal:11434/v1";
const lmStudioUrl = "http://host.docker.internal:1234/v1";
const providers = [["openai", "OpenAI"], ["gemini", "Google Gemini"], ["compatible", "OpenAI-compatible server"]];
const providerNames = Object.fromEntries(providers);
const textFields = ["provider", "base_url", "model", "api_key", "remove_key"];
const whisperFields = ["whisper_model", "whisper_device", "whisper_vad", "vad_parameters"];

export function textProviderName(connection) {
  if (connection?.base_url === ollamaUrl) return "Ollama";
  if (connection?.base_url === lmStudioUrl) return "LM Studio";
  return providerNames[connection?.provider] || connection?.provider || "";
}

function textRecommendations(descriptions) {
  return [
    {
      name: "Ollama on this computer", provider: "compatible", base_url: ollamaUrl,
      model: descriptions ? "qwen3:4b-instruct-2507-q4_K_M" : "qwen3:8b",
      detail: descriptions ? "Pull this model in Ollama for short descriptions and hashtags."
        : "Pull this model in Ollama. Increase its context window if long transcripts are truncated.",
      url: "https://docs.ollama.com/api/openai-compatibility",
    },
    {
      name: "LM Studio on this computer", provider: "compatible", base_url: lmStudioUrl, model: "",
      detail: "Load an instruction-tuned text model, allow connections from Docker, then load its model ID.",
      url: "https://lmstudio.ai/docs/developer/core/server",
    },
    {
      name: "OpenAI", provider: "openai", base_url: "", model: "gpt-6-luna",
      detail: "Efficient cloud text model. Requires your OpenAI API key.",
      url: "https://developers.openai.com/api/docs/models/gpt-6-luna",
    },
    {
      name: "Google Gemini", provider: "gemini", base_url: "",
      model: descriptions ? "gemini-3.1-flash-lite" : "gemini-3.8-flash",
      detail: descriptions ? "A lightweight cloud model for short copy. Requires a Gemini API key."
        : "A cloud model for transcript analysis. Requires a Gemini API key.",
      url: "https://ai.google.dev/gemini-api/docs/models",
    },
  ];
}

export function SettingsScope({ installation, onChange }) {
  return <div className="source-switch model-scope" aria-label="Settings scope" role="group">
    <button type="button" className="secondary-button" aria-pressed={!installation} onClick={() => onChange(false)}>This workspace</button>
    <button type="button" className="secondary-button" aria-pressed={installation} onClick={() => onChange(true)}>Installation defaults</button>
  </div>;
}

export function TextModelSettings({ resource, installation = false, descriptions = false, ranking, onSave }) {
  const id = useId();
  const form = useModelDraft(resource);
  const [models, setModels] = useState(null);
  const [filter, setFilter] = useState("");
  const { data } = resource;
  const { values } = form;
  if (!data) return <ModelLoadState resource={resource} label={descriptions ? "description model" : "ranking model"} />;

  const query = descriptions ? "?task=descriptions" : installation ? "?scope=installation" : "";
  const route = descriptions ? "description-settings/" : `ai-settings/${query}`;
  const previous = data.settings;
  const profile = installation ? data.profiles?.[values.provider] : null;
  const credentialSaved = !values.remove_key && (profile
    ? profile.has_key && values.base_url === profile.base_url
    : values.provider === previous.provider && values.base_url === previous.base_url &&
      (descriptions ? data.has_key : data.connection?.key_source === "saved"));
  const connection = descriptions && !previous.provider
    ? (ranking?.connection?.ready && { provider: textProviderName(ranking.connection),
        model: previous.model || ranking.connection.model })
    : data.connection;
  const connectionReady = descriptions ? Boolean(connection) : connection.ready;
  const modelDefault = values.provider
    ? (profile?.model || (descriptions ? ranking : data)?.provider_defaults?.[values.provider])
    : descriptions ? ranking?.connection?.model : data.installation?.model;
  const savedModel = connection?.model || previous.model || "Choose a model";
  const inheritLabel = descriptions ? "Reuse moment detection connection" : "Use installation defaults";
  const connectionLabel = descriptions
    ? (previous.provider ? textProviderName(previous) : connection?.provider)
    : textProviderName(connection);
  const payload = Object.fromEntries([...textFields, ...(descriptions ? ["automatic"] : [])]
    .filter((field) => field in values).map((field) => [field, values[field]]));
  const matching = models?.filter((model) => model.toLowerCase().includes(filter.trim().toLowerCase())) || [];

  function applyConnection(fields) {
    const endpointChanged = fields.provider !== values.provider || fields.base_url !== values.base_url;
    form.change({ ...fields, ...(endpointChanged ? { api_key: "", remove_key: false } : {}) });
    if (endpointChanged) {
      setModels(null);
      setFilter("");
    }
  }

  function chooseProvider(provider) {
    const selected = provider === previous.provider ? previous : data.profiles?.[provider];
    applyConnection({ provider, base_url: selected?.base_url || "", model: selected?.model || "" });
  }

  return <>
    <SavedModel model={savedModel} detail={connectionReady ? connectionLabel : "Connection setup needed"} />
    <form onSubmit={(event) => { event.preventDefault(); if (form.changed) form.run("save", route, payload, onSave); }}>
      <fieldset className="control-group model-config" disabled={Boolean(form.busy)}>
        <legend className="sr-only">Text model configuration</legend>
        <div className="ai-fields">
          <SelectField label="Connection" value={values.provider}
            choices={installation ? providers : [["", inheritLabel], ...providers]} onChange={chooseProvider} />
          <div>
            <label htmlFor={`${id}-model`}>Model ID</label>
            <div className="input-wrap"><input id={`${id}-model`} value={values.model} spellCheck={false}
              placeholder={modelDefault ? `Default · ${modelDefault}` : "Enter or load a model ID"} maxLength={200}
              onChange={(event) => form.change({ model: event.target.value })} /></div>
          </div>
          {values.provider === "compatible" && <div>
            <label htmlFor={`${id}-url`}>API base URL</label>
            <div className="input-wrap"><input id={`${id}-url`} inputMode="url" spellCheck={false}
              placeholder={data.installation?.base_url || ollamaUrl}
              value={values.base_url} maxLength={500}
              onChange={(event) => applyConnection({ provider: values.provider, base_url: event.target.value })} /></div>
            <small className="ai-help">Use the address reachable by the Cutframe backend. For Docker Desktop, use host.docker.internal for a server on this computer.</small>
          </div>}
          {values.provider && <div>
            <label htmlFor={`${id}-key`}>API key</label>
            <div className="input-wrap"><input id={`${id}-key`} type="password" autoComplete="new-password"
              value={values.api_key || ""} maxLength={1000} placeholder={credentialSaved ? "Saved credential" : "Enter provider key"}
              onChange={(event) => { form.change({ api_key: event.target.value, remove_key: false }); setModels(null); }} /></div>
            <small className="ai-help">{values.provider === "compatible"
              ? "Optional when the server accepts unauthenticated requests."
              : installation ? "Leave blank to keep this provider’s saved credential."
                : "Leave blank to reuse the matching installation credential."}</small>
            {credentialSaved && <button type="button" className="secondary-button ai-remove-key"
              onClick={() => { form.change({ api_key: "", remove_key: true }); setModels(null); }}>Remove saved key</button>}
            {values.remove_key && <small className="ai-help">Save to remove the stored credential.</small>}
          </div>}
        </div>
        <div className="model-picker">
          <div className="model-picker-controls">
            <button className="secondary-button" type="button"
              onClick={() => form.run("models", `ai-settings/models/${query}`, payload, (result) => {
                setModels(result.models);
                setFilter("");
              })}>
              <RefreshCw size={16} aria-hidden="true" /> {form.busy === "models" ? "Loading models…" : models ? "Refresh models" : "Load models"}
            </button>
          </div>
          {models?.length > 12 && <div className="input-wrap">
            <input type="search" aria-label="Filter available models" placeholder="Filter available models" value={filter}
              onChange={(event) => setFilter(event.target.value)} />
          </div>}
          {models?.length > 0 && <SelectField label="Available models" value={matching.includes(values.model) ? values.model : ""}
            choices={[["", matching.length ? "Choose from server" : "No matches"], ...matching.map((model) => [model, model])]}
            onChange={(model) => { if (model) form.change({ model }); }} />}
          {models?.length === 0 && <small className="ai-help" role="status">The server reported no text models. Enter its model ID directly.</small>}
        </div>
        <ModelRecommendations choices={textRecommendations(descriptions)} apply={applyConnection} />
        {descriptions && <label className="preference-check">
          <input type="checkbox" checked={values.automatic} onChange={(event) => form.change({ automatic: event.target.checked })} />
          <span>Write descriptions for new renders<small>Uses one model request per batch of up to 12 finished videos.</small></span>
        </label>}
      </fieldset>
      <ModelActions form={form} label={descriptions ? "Save description model" : "Save ranking model"}
        onCheck={() => form.run("check", `ai-settings/check/${query}`, payload)} />
    </form>
  </>;
}

const whisperRecommendations = [
  { name: "Fast CPU", model: "base", device: "cpu", detail: "A starting point for everyday captions and speech detection.", url: "https://github.com/SYSTRAN/faster-whisper" },
  { name: "More detail", model: "small", detail: "Allow more processing time for harder audio." },
  { name: "GPU with CUDA", model: "large-v3", device: "cuda", detail: "Use a larger model when the worker has CUDA access and enough memory." },
];

export function TranscriptionSettings({ resource, installation = false, onSave }) {
  const id = useId();
  const form = useModelDraft(resource);
  const { data } = resource;
  const { values } = form;
  if (!data) return <ModelLoadState resource={resource} label="transcription" />;
  const savedDevice = data.settings.whisper_device || data.whisper.device;
  const query = installation ? "?scope=installation&task=transcription" : "?task=transcription";
  const payload = Object.fromEntries(whisperFields.map((key) => [key, values[key]]));
  return <>
    <SavedModel model={data.settings.whisper_model || data.whisper.model}
      detail={`Local Whisper · ${savedDevice === "auto" ? "Auto device" : savedDevice.toUpperCase()}`} />
    <form onSubmit={(event) => { event.preventDefault(); if (form.changed) form.run("save", `ai-settings/${query}`, payload, onSave); }}>
      <fieldset className="control-group model-config" disabled={Boolean(form.busy)}>
        <legend className="sr-only">Whisper model configuration</legend>
        <div className="ai-fields">
          <div>
            <label htmlFor={`${id}-model`}>Whisper model or Hugging Face ID</label>
            <div className="input-wrap"><input id={`${id}-model`} list={`${id}-models`} spellCheck={false}
              value={values.whisper_model} maxLength={120} placeholder={`Default · ${data.whisper.model}`}
              onChange={(event) => form.change({ whisper_model: event.target.value })} /></div>
            <datalist id={`${id}-models`}>{data.whisper.models.map((model) => <option key={model} value={model} />)}</datalist>
          </div>
          <SelectField label="Whisper device" value={values.whisper_device}
            choices={[["", installation ? "Automatic" : `Installation default (${data.whisper.device})`],
              ["auto", "Auto-detect"], ["cpu", "CPU"], ["cuda", "CUDA"]]}
            onChange={(whisper_device) => form.change({ whisper_device })} />
        </div>
        <ModelRecommendations choices={whisperRecommendations} apply={({ model, device }) =>
          form.change({ whisper_model: model, ...(device ? { whisper_device: device } : {}) })} />
        <details className="model-recommendations">
          <summary>Voice activity detection</summary>
          <div className="ai-fields model-config">
            <SelectField label="Filter non-speech" value={values.whisper_vad}
              choices={[["", installation ? "Default (off)" : `Installation default (${data.whisper.vad})`], ["off", "Off"], ["on", "On"]]}
              onChange={(whisper_vad) => form.change({ whisper_vad })} />
            <div>
              <label htmlFor={`${id}-vad`}>Advanced VAD parameters</label>
              <div className="input-wrap"><input id={`${id}-vad`} spellCheck={false} placeholder={'{"threshold": 0.5}'}
                value={typeof values.vad_parameters === "string" ? values.vad_parameters :
                  values.vad_parameters ? JSON.stringify(values.vad_parameters) : ""}
                onChange={(event) => form.change({ vad_parameters: event.target.value })} /></div>
            </div>
          </div>
        </details>
      </fieldset>
      <ModelActions form={form} label="Save transcription settings" />
    </form>
  </>;
}
