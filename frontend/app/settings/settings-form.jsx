"use client";

import { useEffect, useState } from "react";
import { Check, RotateCcw, Save } from "lucide-react";
import { usePreferences } from "../preferences";
import RenderOptions, { HighlightOptions, SelectField, studioModes } from "../render-options";
import AISettings from "./ai-settings";
import PublishingSettings from "./publishing-settings";
import StorageSettings from "./storage-settings";
import ThumbnailAISettings from "./thumbnail-ai-settings";
import { settingsSections, useSettingsSection } from "./sections";

const themes = [["light", "Light"], ["dark", "Dark"], ["system", "Match system"]];
const appearanceFields = new Set(["theme", "show_scores"]);

function PreferencesPanel({ section, description, children }) {
  const { defaults, preferences, loading, error, reload, save } = usePreferences();
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState("");
  const values = { ...preferences, ...draft };
  const isAppearance = section === settingsSections.appearance;
  const fields = Object.keys(defaults).filter((field) => appearanceFields.has(field) === isAppearance);
  const changed = fields.some((field) => values[field] !== preferences[field]);
  const custom = fields.some((field) => values[field] !== defaults[field]);

  function change(next) {
    setDraft((previous) => ({ ...previous, ...next }));
    setSaved(false);
    setSaveError("");
  }

  async function submit(event) {
    event.preventDefault();
    if (!changed || saving) return;
    setSaving(true);
    setSaveError("");
    try {
      await save(Object.fromEntries(fields.map((field) => [field, values[field]])));
      setDraft({});
      setSaved(true);
    } catch (failure) {
      setSaveError(Object.values(failure.fields ?? {})[0]?.[0]?.message || failure.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel settings-panel" aria-labelledby={section.id}>
      <div className="panel-heading">
        <span className="heading-icon"><section.Icon size={21} aria-hidden="true" /></span>
        <div>
          <h2 id={section.id} tabIndex={-1}>{section.label}</h2>
          <p>{description}</p>
        </div>
      </div>
      {loading ? <p className="muted" role="status">Loading preferences…</p> :
        error ? (
          <>
            <p className="field-error" role="alert">{error}</p>
            <button className="secondary-button settings-retry" type="button" onClick={reload}>Try again</button>
          </>
        ) : (
          <form onSubmit={submit}>
            <fieldset className="control-group" disabled={saving}>
              <legend className="sr-only">{section.label} preferences</legend>
              {children(values, change)}
            </fieldset>
            <div className="settings-actions">
              <div className="settings-feedback">
                {saveError && <p className="field-error" role="alert">{saveError}</p>}
                <p className="settings-saved" role="status">{saved && <><Check size={17} aria-hidden="true" /> Preferences saved</>}</p>
              </div>
              <div className="settings-buttons">
                <button type="button" className="secondary-button" disabled={saving || !custom}
                  onClick={() => change(Object.fromEntries(fields.map((field) => [field, defaults[field]])))}>
                  <RotateCcw size={16} aria-hidden="true" /> Restore defaults
                </button>
                <button type="submit" className="primary-button" disabled={!changed || saving}>
                  <Save size={17} aria-hidden="true" /> {saving ? "Saving…" : "Save preferences"}
                </button>
              </div>
            </div>
          </form>
        )}
    </section>
  );
}

export default function SettingsForm() {
  const active = useSettingsSection();
  const { render, models, thumbnails, publishing, storage, appearance } = settingsSections;

  useEffect(() => {
    if (window.location.hash !== `#${active.id}`) return;
    window.scrollTo(0, 0);
    document.getElementById(active.id)?.focus({ preventScroll: true });
  }, [active.id]);

  return (
    <section className="settings-page" aria-labelledby="settings-title">
      <header className="settings-title">
        <h1 id="settings-title">Settings</h1>
      </header>
      <nav className="settings-sections" aria-label="Settings sections">
        {Object.values(settingsSections).map(({ id, label, Icon }) => (
          <a key={id} href={`#${id}`} aria-current={active.id === id ? "location" : undefined}>
            <Icon size={18} aria-hidden="true" /><span>{label}</span>
          </a>
        ))}
      </nav>
      <div hidden={active !== render}>
        <PreferencesPanel section={render} description="Starting options for every new render.">
          {(values, change) => <>
            <SelectField label="Default studio mode" value={values.studio_mode} choices={studioModes}
              onChange={(studio_mode) => change({ studio_mode })} />
            <section className="mode-preferences"><h3>Highlights defaults</h3>
              <HighlightOptions value={values} onChange={change} />
            </section>
            <section className="mode-preferences"><h3>Short Clips defaults</h3>
              <RenderOptions value={values} onChange={change} />
              <small className="selection-hint">Language, quality, and captions also apply to Highlights.</small>
            </section>
          </>}
        </PreferencesPanel>
      </div>
      <div hidden={active !== models}>
        <AISettings />
      </div>
      <div hidden={active !== thumbnails}>
        <ThumbnailAISettings />
      </div>
      <div hidden={active !== publishing}>
        <PublishingSettings />
      </div>
      <div hidden={active !== storage}>
        <StorageSettings />
      </div>
      <div hidden={active !== appearance}>
        <PreferencesPanel section={appearance} description="Display preferences for this browser.">
          {(values, change) => (
            <>
              <SelectField label="Theme" value={values.theme} choices={themes}
                onChange={(theme) => change({ theme })} />
              <label className="preference-check">
                <input type="checkbox" checked={values.show_scores}
                  onChange={(event) => change({ show_scores: event.target.checked })} />
                <span>Show ranking scores<small>Display scores on finished clip cards.</small></span>
              </label>
            </>
          )}
        </PreferencesPanel>
      </div>
    </section>
  );
}
