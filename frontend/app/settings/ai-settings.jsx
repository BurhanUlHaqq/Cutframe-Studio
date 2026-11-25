"use client";

import { useEffect, useState } from "react";
import { Check, Cpu, Plug, RefreshCw, Save } from "lucide-react";
import { api, submit } from "../api";
import { SelectField } from "../render-options";

const providers = [
  ["", "Installation defaults"], ["openai", "OpenAI"], ["gemini", "Google Gemini"],
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
  const [scope, setScope] = useState("workspace");
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [models, setModels] = useState(null);
  const [modelFilter, setModelFilter] = useState("");
  const [manualModel, setManualModel] = useState(false);
  const installationScope = scope === "installation";
  const query = installationScope ? "?scope=installation" : "";

  useEffect(() => {
    const abort = new AbortController();
    api(`ai-settings/${query}`, { signal: abort.signal })
      .then(setData)
      .catch((failure) => { if (!abort.signal.aborted) setError(failure.message); });
    return () => abort.abort();
  }, [loadAttempt, query]);

  function selectScope(next) {
    if (next === scope) return;
    setData(null);
    setDraft({});
    setModels(null);
    setModelFilter("");
    setManualModel(false);
    setError("");
    setMessage("");
    setScope(next);
  }

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
      const result = await submit(actions[action] + query, { ...data.settings, ...draft });
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
  const profile = data?.profiles?.[values.provider];
  const savedKey = !values.remove_key && (installationScope
    ? profile?.has_key && values.base_url === profile.base_url
    : data?.connection.key_source === "saved" && values.provider === data.settings.provider &&
      values.base_url === data.settings.base_url);
  const changed = data && (Object.keys(data.settings).some((name) => data.settings[name] !== values[name]) ||
    values.api_key || values.remove_key);
  const installation = data?.installation;
  const installationLabel = providers.find(([provider]) => provider === installation?.provider)?.[1];
  const defaultModel = values.provider ? data?.provider_defaults?.[values.provider] : installation?.model;
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
      <fieldset className="control-group ai-scope" disabled={Boolean(busy)}>
        <legend className="sr-only">AI settings scope</legend>
        <div className="source-switch">
          <button type="button" className="secondary-button" aria-pressed={!installationScope}
            onClick={() => selectScope("workspace")}>This browser</button>
          <button type="button" className="secondary-button" aria-pressed={installationScope}
            onClick={() => selectScope("installation")}>Installation defaults</button>
        </div>
      </fieldset>
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
              <SelectField label="Ranking provider" value={values.provider} choices={installationScope ? providers.slice(1) : providers}
                onChange={(provider) => change({
                  provider, model: provider === data.settings.provider ? data.settings.model : data.profiles?.[provider]?.model || "",
                  base_url: provider === data.settings.provider ? data.settings.base_url : data.profiles?.[provider]?.base_url || "",
                  api_key: "", remove_key: false,
                })} />
              {values.provider ? (
                <>
                  {values.provider === "compatible" && <div>
                    <label htmlFor="api-base-url">API base URL</label>
                    <div className="input-wrap"><input id="api-base-url" inputMode="url" list="api-base-presets" spellCheck={false}
                      value={values.base_url} maxLength={500} placeholder={installation.base_url || endpoints[0][0]}
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
                      : installationScope ? "Blank keeps the saved key for this provider, if present."
                        : "Leave blank to inherit a saved installation key for this provider."}</small>
                    {savedKey && <button type="button" className="secondary-button ai-remove-key"
                      onClick={() => change({ remove_key: true, api_key: "" })}>Remove saved key</button>}
                    {values.remove_key && <small className="ai-help">Save to remove the stored credential.</small>}
                  </div>
                </>
              ) : (
                <p className="ai-environment">{installationLabel
                  ? `Uses the ${installationLabel} connection saved in Installation defaults.`
                  : "Choose a ranking provider under Installation defaults."}</p>
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
            <div className="ai-transcription ai-fields">
              <div>
                <label htmlFor="whisper-model">Whisper model</label>
                <div className="input-wrap">
                  <input id="whisper-model" list="whisper-model-options" spellCheck={false} maxLength={120}
                    placeholder={installationScope ? "base" : data.whisper.model} value={values.whisper_model}
                    onChange={(event) => change({ whisper_model: event.target.value })} />
                </div>
                <datalist id="whisper-model-options">
                  {data.whisper.models.map((model) => <option key={model} value={model} />)}
                </datalist>
              </div>
              <SelectField label="Whisper device" value={values.whisper_device}
                choices={[["", installationScope ? "Automatic" : `Installation default (${data.whisper.device})`],
                  ["auto", "Auto-detect"], ["cpu", "CPU"], ["cuda", "CUDA"]]}
                onChange={(whisper_device) => change({ whisper_device })} />
              <SelectField label="Voice activity detection" value={values.whisper_vad}
                choices={[["", installationScope ? "Default (off)" : `Installation default (${data.whisper.vad})`],
                  ["off", "Off"], ["on", "On"]]} onChange={(whisper_vad) => change({ whisper_vad })} />
              <div>
                <label htmlFor="whisper-vad-settings">Advanced VAD parameters</label>
                <div className="input-wrap"><input id="whisper-vad-settings" spellCheck={false}
                  placeholder={'{"threshold": 0.5}'}
                  value={typeof values.vad_parameters === "string" ? values.vad_parameters :
                    values.vad_parameters ? JSON.stringify(values.vad_parameters) : ""}
                  onChange={(event) => change({ vad_parameters: event.target.value })} /></div>
              </div>
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
