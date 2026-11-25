"use client";

import { useEffect, useState } from "react";
import { FileText, RefreshCw, Save } from "lucide-react";
import { api, submit } from "../api";
import { SelectField } from "../render-options";

const endpoint = "description-settings/";

export default function DescriptionSettings() {
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [models, setModels] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api(endpoint, { signal: controller.signal }).then(setData).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [attempt]);

  const values = { ...data?.settings, ...draft };
  const changed = data && (values.model !== data.settings.model || values.automatic !== data.settings.automatic);

  function change(fields) {
    setDraft((previous) => ({ ...previous, ...fields }));
    setError("");
    setNotice("");
  }

  async function run(action) {
    if (busy) return;
    setBusy(action);
    setError("");
    setNotice("");
    try {
      if (action === "models") {
        const result = await submit("ai-settings/models/", {});
        setModels(result.models);
        if (!result.models.length) setNotice("This provider listed no models. You can enter a model ID manually.");
      } else {
        setData(await submit(endpoint, values));
        setDraft({});
        setNotice("Description settings saved.");
      }
    } catch (failure) {
      setError(Object.values(failure.fields || {})[0]?.[0]?.message || failure.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="panel settings-panel" aria-labelledby="description-settings-title">
      <div className="panel-heading">
        <span className="heading-icon"><FileText size={21} aria-hidden="true" /></span>
        <div><h2 id="description-settings-title" tabIndex={-1}>Descriptions</h2>
          <p>Choose the text model that writes video descriptions and topic hashtags.</p></div>
      </div>
      {!data ? error ? <><p className="field-error" role="alert">{error}</p><button className="secondary-button settings-retry"
        type="button" onClick={() => { setError(""); setAttempt((value) => value + 1); }}>Try again</button></> :
        <p className="muted" role="status">Loading description settings…</p> : (
        <form onSubmit={(event) => { event.preventDefault(); if (changed) run("save"); }}>
          <p className="ai-help description-connection">{data.connection ? `Uses your ${data.connection.provider} text connection.` :
            "Connect a text provider to generate descriptions."} <a href="#ai-settings-title">Open AI model settings</a></p>
          <fieldset className="control-group" disabled={Boolean(busy)}>
            <legend className="sr-only">Description model configuration</legend>
            <div className="ai-fields">
              <div>
                <label htmlFor="description-model-id">Description model ID</label>
                <div className="input-wrap"><input id="description-model-id" value={values.model} maxLength={200}
                  spellCheck={false} placeholder="Leave blank to use the ranking model"
                  onChange={(event) => change({ model: event.target.value })} /></div>
              </div>
              {models?.length > 0 && <SelectField label="Available text models"
                value={models.includes(values.model) ? values.model : ""}
                choices={[["", "Use ranking model"], ...models.map((model) => [model, model])]}
                onChange={(model) => change({ model })} />}
            </div>
            <button className="secondary-button description-model-list" type="button" onClick={() => run("models")}>
              <RefreshCw size={16} aria-hidden="true" /> {busy === "models" ? "Loading models…" : models ? "Refresh models" : "Load models"}
            </button>
            <label className="preference-check">
              <input type="checkbox" checked={values.automatic}
                onChange={(event) => change({ automatic: event.target.checked })} />
              <span>Write AI descriptions for new renders<small>Uses one model request per batch of up to 12 finished videos.</small></span>
            </label>
          </fieldset>
          <div className="settings-actions">
            <div className="settings-feedback">
              {error && <p className="field-error" role="alert">{error}</p>}
              {notice && <p className="settings-saved" role="status">{notice}</p>}
            </div>
            <button className="primary-button" type="submit" disabled={!changed || Boolean(busy)}>
              <Save size={17} aria-hidden="true" /> {busy === "save" ? "Saving…" : "Save description settings"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
