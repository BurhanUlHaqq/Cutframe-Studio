"use client";

import { useState } from "react";
import { Check, Monitor, RotateCcw, Save, SlidersHorizontal } from "lucide-react";
import { usePreferences } from "../preferences";
import RenderOptions, { SelectField } from "../render-options";
import AISettings from "./ai-settings";

const themes = [["light", "Light"], ["dark", "Dark"], ["system", "Match system"]];

export default function SettingsForm() {
  const { defaults, preferences, loading, error, reload, save } = usePreferences();
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState("");
  const values = draft ?? preferences;
  const fields = Object.keys(defaults);
  const changed = fields.some((field) => values[field] !== preferences[field]);
  const custom = fields.some((field) => values[field] !== defaults[field]);

  function change(next) {
    setDraft({ ...values, ...next });
    setSaved(false);
    setSaveError("");
  }

  async function submit(event) {
    event.preventDefault();
    if (!changed || saving) return;
    setSaving(true);
    setSaveError("");
    try {
      await save(values);
      setDraft(null);
      setSaved(true);
    } catch (failure) {
      setSaveError(Object.values(failure.fields ?? {})[0]?.[0]?.message || failure.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="settings-page" aria-labelledby="settings-title">
      <header className="settings-title">
        <h1 id="settings-title">Settings</h1>
        <p>Set your render defaults, AI models, and workspace preferences.</p>
      </header>
      {loading ? <p className="panel empty-state" role="status">Loading preferences…</p> :
        error ? (
          <div className="panel settings-panel">
            <p role="alert">{error}</p>
            <button className="secondary-button settings-retry" type="button" onClick={reload}>Try again</button>
          </div>
        ) : (
          <form onSubmit={submit}>
            <fieldset className="control-group" disabled={saving}>
              <legend className="sr-only">Workspace preferences</legend>
              <div className="settings-grid">
                <section className="panel settings-panel" aria-labelledby="render-defaults-title">
                  <div className="panel-heading">
                    <span className="heading-icon"><SlidersHorizontal size={21} aria-hidden="true" /></span>
                    <div>
                      <h2 id="render-defaults-title">Render defaults</h2>
                      <p>Pre-filled when you start a new render.</p>
                    </div>
                  </div>
                  <RenderOptions value={values} onChange={change} />
                </section>
                <section className="panel settings-panel" aria-labelledby="appearance-title">
                  <div className="panel-heading">
                    <span className="heading-icon"><Monitor size={21} aria-hidden="true" /></span>
                    <div>
                      <h2 id="appearance-title">Appearance</h2>
                      <p>Preferences for this browser.</p>
                    </div>
                  </div>
                  <SelectField label="Theme" value={values.theme} choices={themes}
                    onChange={(theme) => change({ theme })} />
                  <label className="preference-check">
                    <input type="checkbox" checked={values.show_scores}
                      onChange={(event) => change({ show_scores: event.target.checked })} />
                    <span>Show ranking scores<small>Display scores on finished clip cards.</small></span>
                  </label>
                </section>
              </div>
            </fieldset>
            <div className="settings-actions">
              <div className="settings-feedback">
                {saveError && <p className="field-error" role="alert">{saveError}</p>}
                <p className="settings-saved" role="status">{saved && <><Check size={17} aria-hidden="true" /> Preferences saved</>}</p>
              </div>
              <div className="settings-buttons">
                <button type="button" className="secondary-button" disabled={saving || !custom}
                  onClick={() => change(defaults)}><RotateCcw size={16} aria-hidden="true" /> Restore defaults</button>
                <button type="submit" className="primary-button" disabled={!changed || saving}>
                  <Save size={17} aria-hidden="true" /> {saving ? "Saving…" : "Save preferences"}
                </button>
              </div>
            </div>
          </form>
        )}
      <AISettings />
    </section>
  );
}
