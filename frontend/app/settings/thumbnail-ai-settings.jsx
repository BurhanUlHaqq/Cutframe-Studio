"use client";

import { useEffect, useState } from "react";
import { Check, Image, Save } from "lucide-react";
import { api, submit } from "../api";
import { SelectField } from "../render-options";

const endpoint = "thumbnail-ai-settings/";
const providers = [["", "Choose a provider"], ["openai", "OpenAI"], ["compatible", "OpenAI-compatible image server"]];
const qualityLevels = [["low", "Low"], ["medium", "Medium"], ["high", "High"]];
const customModel = "__custom__";

export default function ThumbnailAISettings() {
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [custom, setCustom] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    api(endpoint, { signal: abort.signal }).then(setData).catch((failure) => {
      if (!abort.signal.aborted) setError(failure.message);
    });
    return () => abort.abort();
  }, [attempt]);

  const values = { ...data?.settings, ...draft };
  const defaultModel = data?.openai_models[0];
  const openAI = values.provider === "openai";
  const knownModel = data?.openai_models.includes(values.model);
  const manualModel = !openAI || custom || !knownModel;
  const savedKey = data?.has_key && values.provider === data.settings.provider &&
    values.base_url === data.settings.base_url && !values.remove_key;
  const changed = data && (Object.keys(data.settings).some((key) => values[key] !== data.settings[key]) ||
    values.api_key || values.remove_key);

  function change(next) {
    setDraft((previous) => ({ ...previous, ...next }));
    setSaved(false);
    setError("");
  }

  function chooseProvider(provider) {
    setCustom(false);
    change({
      provider, model: provider === data.settings.provider ? data.settings.model : provider === "openai" ? defaultModel : "",
      base_url: provider === data.settings.provider ? data.settings.base_url : "", api_key: "", remove_key: false,
    });
  }

  async function save(event) {
    event.preventDefault();
    if (!changed || saving) return;
    setSaving(true);
    setError("");
    try {
      setData(await submit(endpoint, values));
      setDraft({});
      setSaved(true);
    } catch (failure) {
      setError(Object.values(failure.fields || {})[0]?.[0]?.message || failure.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel settings-panel" aria-labelledby="thumbnail-ai-settings-title">
      <div className="panel-heading">
        <span className="heading-icon"><Image size={21} aria-hidden="true" /></span>
        <div><h2 id="thumbnail-ai-settings-title" tabIndex={-1}>Thumbnails</h2>
          <p>Choose the image model that creates artwork for your short clips and highlights.</p></div>
      </div>
      {!data ? (error ? <><p className="field-error" role="alert">{error}</p>
        <button className="secondary-button settings-retry" type="button"
          onClick={() => { setError(""); setAttempt((n) => n + 1); }}>Try again</button></> :
        <p className="muted" role="status">Loading thumbnail settings…</p>) : (
        <form onSubmit={save}>
          <fieldset className="control-group" disabled={saving}>
            <legend className="sr-only">Thumbnail model configuration</legend>
            <div className="ai-fields">
              <SelectField label="Image provider" value={values.provider} choices={providers} onChange={chooseProvider} />
              {openAI && <SelectField label="Image model" value={manualModel ? customModel : values.model}
                choices={[...data.openai_models.map((model) => [model, model]), [customModel, "Enter a model ID"]]}
                onChange={(model) => {
                  setCustom(model === customModel);
                  change({ model: model === customModel ? "" : model });
                }} />}
              {values.provider === "compatible" && <div>
                <label htmlFor="thumbnail-base-url">Image API base URL</label>
                <div className="input-wrap"><input id="thumbnail-base-url" value={values.base_url} maxLength={500}
                  placeholder="http://host.docker.internal:7860/v1" inputMode="url" spellCheck={false} required
                  onChange={(event) => change({ base_url: event.target.value })} /></div>
                <small className="ai-help">Use a server that supports the OpenAI Images API and returns inline image data.</small>
              </div>}
              {values.provider && manualModel && <div>
                <label htmlFor="thumbnail-model-id">Image model ID</label>
                <div className="input-wrap"><input id="thumbnail-model-id" value={values.model} maxLength={200}
                  placeholder="Model ID from your image provider" spellCheck={false} required
                  onChange={(event) => change({ model: event.target.value })} /></div>
              </div>}
              {values.provider && <div>
                <label htmlFor="thumbnail-api-key">Image API key</label>
                <div className="input-wrap"><input id="thumbnail-api-key" value={values.api_key || ""} maxLength={1000}
                  type="password" autoComplete="new-password" placeholder={savedKey ? "Saved credential" : "Optional"}
                  onChange={(event) => change({ api_key: event.target.value, remove_key: false })} /></div>
                <small className="ai-help">{openAI ? "Leave blank to use the OpenAI key saved under AI models." :
                  "Leave blank if your server allows unauthenticated requests."}</small>
                {savedKey && <button className="secondary-button ai-remove-key" type="button"
                  onClick={() => change({ api_key: "", remove_key: true })}>Remove saved key</button>}
                {values.remove_key && <small className="ai-help">Save to remove the stored key.</small>}
              </div>}
              {openAI && <SelectField label="Image quality" value={values.quality} choices={qualityLevels}
                onChange={(quality) => change({ quality })} />}
            </div>
            {values.provider && <label className="preference-check">
              <input type="checkbox" checked={values.automatic}
                onChange={(event) => change({ automatic: event.target.checked })} />
              <span>Automatically generate covers for new renders<small>Uses one image generation per finished video.</small></span>
            </label>}
          </fieldset>
          <div className="settings-actions">
            <div className="settings-feedback">
              {error && <p className="field-error" role="alert">{error}</p>}
              {saved && <p className="settings-saved" role="status"><Check size={17} aria-hidden="true" /> Thumbnail settings saved.</p>}
              {!changed && openAI && !data.ready && <p className="ai-help">Save an OpenAI key to generate covers.</p>}
            </div>
            <button className="primary-button" disabled={!changed || saving}>
              <Save size={17} aria-hidden="true" /> {saving ? "Saving…" : "Save thumbnail settings"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
