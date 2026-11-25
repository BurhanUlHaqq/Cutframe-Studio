"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { HardDrive, Save } from "lucide-react";
import { api, submit } from "../api";

export default function StorageSettings() {
  const [limit, setLimit] = useState(null);
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api("storage-settings/", { signal: controller.signal })
      .then(({ max_upload_mib }) => setLimit(max_upload_mib))
      .catch((failure) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [attempt]);

  async function save(event) {
    event.preventDefault();
    if (busy || draft === null) return;
    setBusy(true);
    setError("");
    try {
      const data = await submit("storage-settings/", { max_upload_mib: draft });
      setLimit(data.max_upload_mib);
      setDraft(null);
      setSaved(true);
    } catch (failure) {
      setError(Object.values(failure.fields || {})[0]?.[0]?.message || failure.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel settings-panel" aria-labelledby="storage-settings-title">
      <div className="panel-heading">
        <span className="heading-icon"><HardDrive size={21} aria-hidden="true" /></span>
        <div><h2 id="storage-settings-title" tabIndex={-1}>Storage</h2>
          <p>Set the maximum size of an uploaded video for this installation.</p></div>
      </div>
      <Link href="/library?sort=largest" className="secondary-button storage-library-link">Manage render files</Link>
      {limit === null ? (error ? <><p className="field-error" role="alert">{error}</p>
        <button className="secondary-button settings-retry" type="button" onClick={() => {
          setError(""); setAttempt((n) => n + 1);
        }}>Try again</button></> : <p className="muted" role="status">Loading storage settings…</p>) : (
        <form onSubmit={save}>
          <fieldset className="control-group" disabled={busy}>
            <legend className="sr-only">Upload limit</legend>
            <label htmlFor="upload-limit">Maximum upload size</label>
            <div className="input-wrap number-with-unit">
              <input id="upload-limit" type="number" min={1} max={16384} required value={draft ?? limit}
                onChange={(event) => { setDraft(event.target.value); setError(""); setSaved(false); }} />
              <span aria-hidden="true">MiB</span>
            </div>
          </fieldset>
          <div className="settings-actions">
            <div className="settings-feedback">
              {error && <p className="field-error" role="alert">{error}</p>}
              {saved && <p className="settings-saved" role="status">Upload limit saved.</p>}
            </div>
            <button className="primary-button" disabled={busy || draft === null || Number(draft) === limit}>
              <Save size={16} aria-hidden="true" /> {busy ? "Saving…" : "Save upload limit"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
