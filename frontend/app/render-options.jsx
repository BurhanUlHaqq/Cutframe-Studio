"use client";

import { useId } from "react";
import { ChevronDown, Minus, Plus } from "lucide-react";

const ratios = [
  ["9:16", "Vertical"],
  ["1:1", "Square"],
  ["16:9", "Landscape"],
];

const qualities = [["480", "480p"], ["720", "720p"], ["1080", "1080p"]];
const framings = [["auto", "Smart framing"], ["stream", "Screen + streamer"], ["center", "Center crop"], ["fit", "Fit with blur"]];
const captionStyles = [["classic", "Classic captions"], ["highlight", "Highlighted words"], ["off", "Off"]];
const selectionModes = [["count", "By clip count"], ["score", "By viral score"]];
const gameplaySelection = [["count", "By clip count"], ["score", "By activity score"]];
export const editingStyles = {
  balanced: ["Balanced", "Finds standalone moments and follows your framing and caption settings."],
  fast: ["Fast Cuts", "Looks for a quick hook and payoff, tightens quiet gaps, and uses shorter caption groups."],
  podcast: ["Podcast", "Keeps question-and-answer context, steadies framing, and levels speech."],
  gameplay: ["Gameplay", "Finds screen action locally and keeps the game in view. A stable webcam can sit above it."],
  tutorial: ["Tutorial", "Keeps the full demonstration in frame and looks for a complete step and result."],
};
export const studioModes = [["shorts", "Short Clips"], ["highlights", "Highlights"]];
const highlightMethods = [["transcript", "AI transcript"], ["activity", "Local visual activity"]];

const languages = [
  ["", "Auto-detect"], ["en", "English"], ["ur", "Urdu"], ["hi", "Hindi"],
  ["es", "Spanish"], ["fr", "French"], ["ar", "Arabic"], ["de", "German"],
  ["zh", "Chinese"], ["bn", "Bengali"], ["it", "Italian"], ["ja", "Japanese"],
  ["ko", "Korean"], ["pt", "Portuguese"], ["ru", "Russian"], ["tr", "Turkish"],
];

export function SelectField({ label, value, choices, onChange }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <div className="select-wrap">
        <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
          {choices.map(([option, name]) => <option key={option} value={option}>{name}</option>)}
        </select>
        <ChevronDown size={16} aria-hidden="true" />
      </div>
    </div>
  );
}

export function HighlightOptions({ value, onChange, showShared = false }) {
  const id = useId();
  const minutes = value.highlight_minutes;
  const language = value.language;
  const languageOptions = languages.some(([code]) => code === language) ? languages : [...languages, [language, language.toUpperCase()]];
  return (
    <div className="highlight-options">
      <div>
        <label htmlFor={id}>Maximum reel length</label>
        <div className="input-wrap number-with-unit">
          <input id={id} type="number" min={1} max={30} required value={minutes ?? ""}
            aria-describedby={`${id}-hint`}
            onChange={(event) => onChange({ highlight_minutes: event.target.value === "" ? null : Number(event.target.value) })} />
          <span aria-hidden="true">min</span>
        </div>
        <div className="highlight-presets" role="group" aria-label="Reel length presets">
          {[3, 10, 20, 30].map((length) => <button key={length} type="button" className="secondary-button"
            aria-pressed={minutes === length} onClick={() => onChange({ highlight_minutes: length })}>{length} min</button>)}
        </div>
        <small id={`${id}-hint`}>Choose 1–30 minutes. The reel can finish sooner when there are fewer useful moments.</small>
      </div>
      <div className="options highlight-production">
        <SelectField label="Moment selection" value={value.highlight_method} choices={highlightMethods}
          onChange={(next) => onChange({ highlight_method: next })} />
        {showShared && <SelectField label="Output quality" value={value.quality} choices={qualities}
          onChange={(next) => onChange({ quality: next })} />}
      </div>
      <small>{value.highlight_method === "activity" ? "Uses motion across the source. Works without a ranking provider."
        : "Uses the configured ranking model to find complete story beats. Falls back to visual activity when dialogue or a configured model is unavailable."}</small>
      {showShared && (
        <div className="options highlight-production">
          <SelectField label="Captions" value={value.caption_style} choices={captionStyles}
            onChange={(next) => onChange({ caption_style: next })} />
          {(value.highlight_method === "transcript" || value.caption_style !== "off") &&
            <SelectField label="Transcription language" value={language} choices={languageOptions}
              onChange={(next) => onChange({ language: next })} />}
        </div>
      )}
    </div>
  );
}

export default function RenderOptions({ value, onChange, compact = false, showQuality = true }) {
  const id = useId();
  const { clip_count: count, aspect_ratio: ratio, quality, language, framing, caption_style } = value;
  const { selection_mode, min_viral_score, min_clip_seconds, max_clip_seconds } = value;
  const style = value.editing_style || "balanced";
  const gameplay = style === "gameplay";
  const byScore = selection_mode === "score";
  const selectedLanguage = languages.find(([code]) => code === language);
  const languageOptions = selectedLanguage ? languages : [...languages, [language, language.toUpperCase()]];
  const languageField = <SelectField label="Transcription language" value={language}
    choices={languageOptions} onChange={(next) => onChange({ language: next })} />;

  return (
    <>
      <div className="clip-edit-style">
        <SelectField label="Editing style" value={style}
          choices={Object.entries(editingStyles).map(([key, [label]]) => [key, label])}
          onChange={(editing_style) => onChange({ editing_style })} />
        <small className="selection-hint">{editingStyles[style][1]}</small>
      </div>
      <div className="clip-selection">
        <SelectField label="Clip selection" value={selection_mode} choices={gameplay ? gameplaySelection : selectionModes}
          onChange={(next) => onChange({ selection_mode: next, min_viral_score: min_viral_score ?? 80 })} />
      </div>
      <div className="options">
        {byScore ? (
          <div>
            <label htmlFor={`${id}-viral-score`}>{gameplay ? "Minimum activity score" : "Minimum viral score"}</label>
            <div className="input-wrap number-with-unit">
              <input id={`${id}-viral-score`} type="number" min={0} max={100} required
                value={min_viral_score ?? ""} aria-describedby={`${id}-viral-hint`}
                onChange={(event) => onChange({ min_viral_score: event.target.value === "" ? null : Number(event.target.value) })} />
              <span aria-hidden="true">/ 100</span>
            </div>
          </div>
        ) : (
          <div>
            <label htmlFor={`${id}-count`}>Clip count</label>
            <div className="stepper">
              <button type="button" aria-label="Fewer clips" disabled={count <= 1}
                onClick={() => onChange({ clip_count: count - 1 })}><Minus size={16} aria-hidden="true" /></button>
              <output id={`${id}-count`} aria-live="polite">{count}</output>
              <button type="button" aria-label="More clips" disabled={count >= 10}
                onClick={() => onChange({ clip_count: count + 1 })}><Plus size={16} aria-hidden="true" /></button>
            </div>
          </div>
        )}
        {showQuality && <SelectField label="Source quality" value={quality} choices={qualities}
          onChange={(next) => onChange({ quality: next })} />}
      </div>
      {byScore && <small className="selection-hint" id={`${id}-viral-hint`}>{gameplay
        ? "Activity scores come from on-screen motion and changes in audio."
        : "Keeps every ranked highlight at or above this score."}</small>}
      <fieldset className="ratio-field">
        <legend>Aspect ratio</legend>
        <div className="ratio-options">
          {ratios.map(([option, label]) => (
            <label className={`ratio-choice${option === ratio ? " selected" : ""}`} key={option}>
              <input type="radio" name={`${id}-ratio`} value={option} checked={option === ratio}
                onChange={() => onChange({ aspect_ratio: option })} />
              <span className={`ratio-icon ratio-${label.toLowerCase()}`} aria-hidden="true" />
              <span><strong>{option}</strong><small>{label}</small></span>
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="length-field">
        <legend>Clip length</legend>
        <div className="options length-options">
          {[["min_clip_seconds", "Minimum", min_clip_seconds], ["max_clip_seconds", "Maximum", max_clip_seconds]]
            .map(([field, label, seconds]) => (
              <div key={field}>
                <label htmlFor={`${id}-${field}`}>{label}</label>
                <div className="input-wrap number-with-unit">
                  <input id={`${id}-${field}`} type="number" min={field === "max_clip_seconds" ? min_clip_seconds || 1 : 1}
                    max={180} placeholder="Auto" value={seconds ?? ""} aria-describedby={`${id}-length-hint`}
                    onChange={(event) => onChange({ [field]: event.target.value === "" ? null : Number(event.target.value) })} />
                  <span aria-hidden="true">sec</span>
                </div>
              </div>
            ))}
        </div>
        <small id={`${id}-length-hint`}>Leave both blank for automatic length.</small>
      </fieldset>
      <div className="options production-options">
        <SelectField label="Framing" value={framing} choices={framings}
          onChange={(next) => onChange({ framing: next })} />
        <SelectField label="Captions" value={caption_style} choices={captionStyles}
          onChange={(next) => onChange({ caption_style: next })} />
      </div>
      {framing === "stream" && <small className="selection-hint">
        {ratio === "9:16" ? "Keeps wide gameplay or a screen share in the middle, with the webcam above." :
          "The stacked layout works with wide footage in 9:16. This aspect ratio uses Fit with blur."}
      </small>}
      {compact ? (
        <details className="advanced">
          <summary>Language options <ChevronDown size={15} aria-hidden="true" />
            {language && <span className="language-selection">{selectedLanguage?.[1] || language.toUpperCase()}</span>}
          </summary>
          {languageField}
        </details>
      ) : <div className="language-field">{languageField}</div>}
    </>
  );
}
