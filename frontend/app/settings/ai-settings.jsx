"use client";

import { useEffect, useState } from "react";
import { Check, Cpu, Plug, RefreshCw, Save } from "lucide-react";
import { api, submit } from "../api";
import { SelectField } from "../render-options";

const providers = [
  ["", "Server environment"], ["openai", "OpenAI"], ["gemini", "Google Gemini"],
  ["compatible", "OpenAI-compatible endpoint"],
];
const endpoints = [
  ["http://host.docker.internal:11434/v1", "Ollama on your computer"],
  ["http://host.docker.internal:1234/v1", "LM Studio on your computer"],
  ["https://openrouter.ai/api/v1", "OpenRouter"],
  ["https://api.groq.com/openai/v1", "Groq"],
];
const actions = { save: "ai-settings/", check: "ai-settings/check/", models: "ai-settings/models/" };

export default function AISettings() {
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [models, setModels] = useState(null);
  const [modelFilter, setModelFilter] = useState("");
  const [manualModel, setManualModel] = useState(false);

  useEffect(() => {
    const abort = new AbortController();
    api("ai-settings/", { signal: abort.signal })
      .then(setData)
      .catch((failure) => { if (!abort.signal.aborted) setError(failure.message); });
    return () => abort.abort();
  }, [loadAttempt]);

  function change(fields) {
    setDraft((previous) => ({ ...previous, ...fields }));
    if (["provider", "base_url", "api_key", "remove_key"].some((field) => field in fields)) {
      setModels(null);
      setModelFilter("");
    }
    setError("");
    setMessage("");
  }

  async function run(action) {
    setBusy(action);
    setError("");
    setMessage("");
    try {
      const result = await submit(actions[action], { ...data.settings, ...draft });
      if (action === "models") {
        setModels(result.models);
        setModelFilter("");
        setManualModel(false);
      } else if (action === "check") {
        setMessage(result.message);
      } else {
        setDraft({});
        setData(result);
        setMessage("AI settings saved.");
      }
    } catch (failure) {
      setError(Object.values(failure.fields || {})[0]?.[0]?.message || failure.message);
    } finally {
      setBusy("");
    }
  }

  const values = { ...data?.settings, ...draft };
  const savedKey = data?.connection.key_source === "saved" && values.provider === data.settings.provider &&
    values.base_url === data.settings.base_url && !values.remove_key;
  const changed = data && (Object.keys(data.settings).some((name) => data.settings[name] !== values[name]) ||
    values.api_key || values.remove_key);
  const environment = data?.environment;
  const environmentLabel = providers.find(([provider]) => provider === environment?.provider)?.[1];
  const defaultModel = values.provider ? data?.provider_defaults?.[values.provider] : environment?.model;
  const filter = modelFilter.trim().toLowerCase();
  const modelChoices = [
    ["", defaultModel ? `Use default (${defaultModel})` : "Choose a model"],
    ...(values.model ? [[values.model, values.model]] : []),
    ...(models || []).filter((id) => id !== values.model && id.toLowerCase().includes(filter)).map((id) => [id, id]),
  ];

  return (
    <section className="panel settings-panel" aria-labelledby="ai-settings-title">
      <div className="panel-heading">
        <span className="heading-icon"><Cpu size={21} aria-hidden="true" /></span>
        <div><h2 id="ai-settings-title" tabIndex={-1}>AI models</h2>
          <p>Choose a provider and model for ranking, plus a local Whisper model for transcription.</p></div>
      </div>
      {!data ? (
        error ? <><p className="field-error" role="alert">{error}</p>
          <button className="secondary-button settings-retry" type="button"
            onClick={() => { setError(""); setLoadAttempt((n) => n + 1); }}>Try again</button></> :
          <p className="muted" role="status">Loading AI settings…</p>
      ) : (
        <form onSubmit={(event) => { event.preventDefault(); if (!busy && changed) run("save"); }}>
          <fieldset className="control-group" disabled={Boolean(busy)}>
            <legend className="sr-only">AI model configuration</legend>
            <div className="ai-fields">
              <SelectField label="Ranking provider" value={values.provider} choices={providers}
                onChange={(provider) => change({
                  provider, model: provider === data.settings.provider ? data.settings.model : "",
                  base_url: provider === data.settings.provider ? data.settings.base_url : "",
                  api_key: "", remove_key: false,
                })} />
              {values.provider ? (
                <>
                  {values.provider === "compatible" && <div>
                    <label htmlFor="api-base-url">API base URL</label>
                    <div className="input-wrap"><input id="api-base-url" inputMode="url" list="api-base-presets" spellCheck={false}
                      value={values.base_url} maxLength={500} placeholder={environment.base_url || endpoints[0][0]}
                      onChange={(event) => change({ base_url: event.target.value })} /></div>
                    <datalist id="api-base-presets">
                      {endpoints.map(([url, label]) => <option key={url} value={url}>{label}</option>)}
                    </datalist>
                  </div>}
                  <div>
                    <label htmlFor="ranking-key">API key</label>
                    <div className="input-wrap"><input id="ranking-key" type="password" autoComplete="new-password"
                      value={values.api_key || ""} maxLength={1000} placeholder={savedKey ? "Saved credential" : "Enter an API key"}
                      onChange={(event) => change({ api_key: event.target.value, remove_key: false })} /></div>
                    <small className="ai-help">{values.provider === "compatible"
                      ? "Leave blank for endpoints that allow unauthenticated requests."
                      : "Leave blank to use the server environment key."}</small>
                    {savedKey && <button type="button" className="secondary-button ai-remove-key"
                      onClick={() => change({ remove_key: true, api_key: "" })}>Remove saved key</button>}
                    {values.remove_key && <small className="ai-help">Save to remove the stored credential.</small>}
                  </div>
                </>
              ) : (
                <p className="ai-environment">{environmentLabel
                  ? `Uses the ${environmentLabel} connection configured on the server.`
                  : "Set a ranking provider in the server environment."}</p>
              )}
            </div>
            <div className="model-picker">
              <div className="model-picker-controls">
                <button className="secondary-button" type="button" onClick={() => run("models")}>
                  <RefreshCw size={16} aria-hidden="true" /> {busy === "models" ? "Loading models…" : models ? "Refresh models" : "Load models"}
                </button>
                <button className="secondary-button" type="button" onClick={() => setManualModel((manual) => !manual)}>
                  {manualModel ? "Use model list" : "Enter model ID manually"}
                </button>
              </div>
              {manualModel ? (
                <div>
                  <label htmlFor="rank-model">Ranking model ID</label>
                  <div className="input-wrap"><input id="rank-model" value={values.model}
                    placeholder={defaultModel || "Model ID from your provider"} spellCheck={false} maxLength={200}
                    onChange={(event) => change({ model: event.target.value })} /></div>
                </div>
              ) : (
                <>
                  {models?.length > 12 && <div className="input-wrap">
                    <input type="search" aria-label="Filter models" placeholder="Filter models" value={modelFilter}
                      onChange={(event) => setModelFilter(event.target.value)} />
                  </div>}
                  <SelectField label="Ranking model" value={values.model} choices={modelChoices}
                    onChange={(model) => change({ model })} />
                  <p className="ai-help" role="status">{models?.length
                    ? `${models.length} model${models.length === 1 ? "" : "s"} listed by the provider.`
                    : models ? "The provider listed no ranking models. You can enter a model ID manually."
                    : "Load models from the selected provider."}</p>
                </>
              )}
            </div>
            <div className="ai-transcription">
              <SelectField label="Whisper model" value={values.whisper_model}
                choices={[["", `Server environment (${data.whisper.model})`], ...data.whisper.models.map((model) => [model, model])]}
                onChange={(whisper_model) => change({ whisper_model })} />
            </div>
          </fieldset>
          <div className="settings-actions">
            <div className="settings-feedback">
              {error && <p className="field-error" role="alert">{error}</p>}
              {message && <p className="settings-saved" role="status"><Check size={17} aria-hidden="true" /> {message}</p>}
            </div>
            <div className="settings-buttons">
              <button className="secondary-button" type="button" disabled={Boolean(busy)} onClick={() => run("check")}>
                <Plug size={17} aria-hidden="true" /> {busy === "check" ? "Checking…" : "Check connection"}
              </button>
              <button className="primary-button" disabled={!changed || Boolean(busy)}>
                <Save size={17} aria-hidden="true" /> {busy === "save" ? "Saving…" : "Save AI settings"}
              </button>
            </div>
          </div>
        </form>
      )}
    </section>
  );
}
