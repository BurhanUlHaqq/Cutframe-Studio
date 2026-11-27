"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { SelectField } from "../render-options";
import { ModelActions, ModelLoadState, ModelRecommendations, SavedModel, useModelDraft } from "./model-controls";

const route = "thumbnail-ai-settings/";
const localImageUrl = "http://host.docker.internal:8080/v1";
const providers = [["", "Frame covers only"], ["openai", "OpenAI"], ["compatible", "OpenAI-compatible image server"]];
const recommendationChoices = [
  {
    name: "LocalAI with FLUX.1 schnell", provider: "compatible", base_url: localImageUrl, model: "flux.1-schnell",
    detail: "Install an image model in LocalAI. Use the exact model ID listed by your server.",
    url: "https://localai.io/docs/models/capabilities/vision/image-generation/",
  },
  {
    name: "OpenAI for cover artwork", provider: "openai", base_url: "", model: "gpt-image-2.6-flare",
    detail: "Cloud image generation for iterating on covers. Each generated image uses your provider credit.",
    url: "https://developers.openai.com/api/docs/models/gpt-image-2.6-flare",
  },
];

export default function ThumbnailAISettings({ resource }) {
  const form = useModelDraft(resource);
  const [models, setModels] = useState(null);
  const [filter, setFilter] = useState("");
  const { data } = resource;
  const { values } = form;
  if (!data) return <ModelLoadState resource={resource} label="thumbnail model" />;
  const openAI = values.provider === "openai";
  const previous = data.settings;
  const hasKey = data.has_key && values.provider === previous.provider &&
    values.base_url === previous.base_url && !values.remove_key;
  const available = models ?? (openAI ? data.openai_models : []);
  const matching = available.filter((model) => model.toLowerCase().includes(filter.trim().toLowerCase()));
  const detail = previous.provider ? data.ready ? previous.provider === "openai" ? "OpenAI" : "Image server"
    : "Connection setup needed" : "AI generation off";

  function applyConnection(fields) {
    const endpointChanged = fields.provider !== values.provider || fields.base_url !== values.base_url;
    form.change({ ...fields, ...(endpointChanged ? { api_key: "", remove_key: false } : {}) });
    if (endpointChanged) {
      setModels(null);
      setFilter("");
    }
  }

  function chooseProvider(provider) {
    applyConnection({ provider, model: provider === previous.provider ? previous.model : provider === "openai" ? data.openai_models[0] : "",
      base_url: provider === previous.provider ? previous.base_url : "" });
  }

  return <>
    <SavedModel model={previous.provider ? previous.model || "Choose a model" : "Frame covers"} detail={detail} />
    <form onSubmit={(event) => { event.preventDefault(); if (form.changed) form.run("save", route, values); }}>
      <fieldset className="control-group model-config" disabled={Boolean(form.busy)}>
        <legend className="sr-only">Thumbnail model configuration</legend>
        <div className="ai-fields">
          <SelectField label="Image connection" value={values.provider} choices={providers} onChange={chooseProvider} />
          {values.provider && <div>
            <label htmlFor="thumbnail-model-id">Image model ID</label>
            <div className="input-wrap"><input id="thumbnail-model-id" value={values.model} maxLength={200}
              placeholder="Enter or load a model ID" spellCheck={false} required
              onChange={(event) => form.change({ model: event.target.value })} /></div>
          </div>}
          {values.provider === "compatible" && <div>
            <label htmlFor="thumbnail-base-url">Image API base URL</label>
            <div className="input-wrap"><input id="thumbnail-base-url" value={values.base_url} maxLength={500}
              placeholder={localImageUrl} inputMode="url" spellCheck={false} required
              onChange={(event) => applyConnection({ provider: values.provider, base_url: event.target.value })} /></div>
            <small className="ai-help">The server must support /images/generations and return inline image data.</small>
          </div>}
          {values.provider && <div>
            <label htmlFor="thumbnail-api-key">Image API key</label>
            <div className="input-wrap"><input id="thumbnail-api-key" value={values.api_key || ""} maxLength={1000}
              type="password" autoComplete="new-password" placeholder={hasKey ? "Saved credential" : "Enter provider key"}
              onChange={(event) => { form.change({ api_key: event.target.value, remove_key: false }); setModels(null); }} /></div>
            <small className="ai-help">{openAI ? "Leave blank to use the saved OpenAI text credential, if available."
              : "Optional when the server accepts unauthenticated requests."}</small>
            {hasKey && <button className="secondary-button ai-remove-key" type="button"
              onClick={() => { form.change({ api_key: "", remove_key: true }); setModels(null); }}>Remove saved key</button>}
            {values.remove_key && <small className="ai-help">Save to remove the stored key.</small>}
          </div>}
          {openAI && <SelectField label="Image quality" value={values.quality}
            choices={[["low", "Low"], ["medium", "Medium"], ["high", "High"]]}
            onChange={(quality) => form.change({ quality })} />}
        </div>
        {values.provider && <div className="model-picker">
          <div className="model-picker-controls">
            <button className="secondary-button" type="button"
              onClick={() => form.run("models", route + "models/", values, (result) => { setModels(result.models); setFilter(""); })}>
              <RefreshCw size={16} aria-hidden="true" /> {form.busy === "models" ? "Loading models…" : models ? "Refresh models" : "Load models"}
            </button>
          </div>
          {available.length > 12 && <div className="input-wrap">
            <input type="search" aria-label="Filter image models" placeholder="Filter image models" value={filter}
              onChange={(event) => setFilter(event.target.value)} />
          </div>}
          {available.length > 0 && <SelectField label={models ? "Available server models" : "OpenAI image models"}
            value={matching.includes(values.model) ? values.model : ""}
            choices={[["", matching.length ? "Choose a model" : "No matches"], ...matching.map((model) => [model, model])]}
            onChange={(model) => { if (model) form.change({ model }); }} />}
          {models?.length === 0 && <small className="ai-help" role="status">The server reported no models. Enter its image model ID directly.</small>}
        </div>}
        <ModelRecommendations choices={recommendationChoices} apply={applyConnection} />
        {values.provider && <label className="preference-check">
          <input type="checkbox" checked={values.automatic}
            onChange={(event) => form.change({ automatic: event.target.checked })} />
          <span>Automatically generate covers for new renders<small>Uses one image generation per finished video.</small></span>
        </label>}
        {values.provider && values.automatic && <div className="thumbnail-cover-limit">
          <label htmlFor="automatic-cover-limit">Maximum automatic covers per render</label>
          <div className="input-wrap"><input id="automatic-cover-limit" type="number" min={1} step={1}
            value={values.automatic_limit ?? ""} placeholder="Every video"
            onChange={(event) => form.change({ automatic_limit: event.target.value === "" ? null : Number(event.target.value) })} /></div>
          <small className="ai-help">Videos beyond the limit use frame covers.</small>
        </div>}
      </fieldset>
      <ModelActions form={form} label="Save thumbnail settings"
        onCheck={values.provider && (() => form.run("check", route + "check/", values))} />
    </form>
  </>;
}
