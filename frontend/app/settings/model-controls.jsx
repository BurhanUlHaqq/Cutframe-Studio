"use client";

import { useEffect, useState } from "react";
import { BookOpen, Check, Plug, Save } from "lucide-react";
import { api, submit } from "../api";

export function useModelResource(endpoint) {
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [attempt, setAttempt] = useState(0);

  function reload() {
    setLoadError("");
    setAttempt((previous) => previous + 1);
  }

  useEffect(() => {
    const controller = new AbortController();
    api(endpoint, { signal: controller.signal }).then(setData).catch((failure) => {
      if (!controller.signal.aborted) setLoadError(failure.message);
    });
    return () => controller.abort();
  }, [attempt, endpoint]);

  return { data, setData, loadError, reload };
}

export function useModelDraft(resource) {
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const saved = resource.data?.settings;
  const values = { ...saved, ...draft };
  const changed = Boolean(saved && Object.entries(draft).some(([key, value]) =>
    key === "api_key" || key === "remove_key" ? Boolean(value) : value !== saved[key]));

  function change(fields) {
    setDraft((current) => ({ ...current, ...fields }));
    setError("");
    setNotice("");
  }

  async function run(action, endpoint, payload, onResult) {
    if (busy) return;
    setBusy(action);
    setError("");
    setNotice("");
    try {
      const result = await submit(endpoint, payload);
      if (action === "save") {
        setDraft({});
        resource.setData(result);
        setNotice("Settings saved");
      }
      if (action === "check") setNotice(result.message);
      onResult?.(result);
    } catch (failure) {
      setError(Object.values(failure.fields || {})[0]?.[0]?.message || failure.message);
    } finally {
      setBusy("");
    }
  }

  return { values, changed, busy, notice, error, change, run };
}

export function ModelLoadState({ resource, label }) {
  return resource.loadError ? <>
    <p className="field-error" role="alert">{resource.loadError}</p>
    <button className="secondary-button settings-retry" type="button" onClick={resource.reload}>Try again</button>
  </> : <p className="muted" role="status">Loading {label} settings…</p>;
}

export function SavedModel({ model, detail }) {
  return <div className="saved-model">
    <div>
      <span className="muted">Saved model</span>
      <strong>{model}</strong>
    </div>
    <span className="saved-model-detail">{detail}</span>
  </div>;
}

export function ModelRecommendations({ choices, apply }) {
  return (
    <details className="model-recommendations">
      <summary><BookOpen size={16} aria-hidden="true" /> Recommended models and setup</summary>
      <ul>
        {choices.map(({ name, model, detail, url, ...fields }) => <li key={name}>
          <div>
            <strong>{name}</strong>
            {model && <code>{model}</code>}
            <small>{detail} {url && <a href={url} target="_blank" rel="noreferrer">Setup guide</a>}</small>
          </div>
          <button className="secondary-button" type="button" aria-label={`Use ${name}`}
            onClick={() => apply({ model, ...fields })}>Use</button>
        </li>)}
      </ul>
    </details>
  );
}

export function ModelActions({ form, label, onCheck }) {
  return (
    <div className="settings-actions">
      <div className="settings-feedback">
        {form.error && <p className="field-error" role="alert">{form.error}</p>}
        {form.notice && <p className="settings-saved" role="status"><Check size={17} aria-hidden="true" />{form.notice}</p>}
        {form.changed && !form.busy && !form.error && !form.notice && <small className="muted">Unsaved changes</small>}
      </div>
      <div className="settings-buttons">
        {onCheck && <button className="secondary-button" type="button" onClick={onCheck} disabled={Boolean(form.busy)}>
          <Plug size={16} aria-hidden="true" /> {form.busy === "check" ? "Checking…" : "Check connection"}
        </button>}
        <button className="primary-button" type="submit" disabled={!form.changed || Boolean(form.busy)}>
          <Save size={16} aria-hidden="true" /> {form.busy === "save" ? "Saving…" : label}
        </button>
      </div>
    </div>
  );
}
