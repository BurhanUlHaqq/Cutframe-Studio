"use client";

import Image from "next/image";
import { useEffect, useId, useRef, useState } from "react";
import { Download, Image as ImageIcon, RefreshCw, Save, Sparkles, X } from "lucide-react";
import { submit } from "../../api";
import { SelectField } from "../../render-options";

const startingDesign = { frame: 0, headline: "", accent: "gold", placement: "auto" };
const accents = [["gold", "Gold"], ["white", "White"], ["mint", "Mint"]];
const placements = [["auto", "Automatic"], ["bottom", "Bottom"], ["top", "Top"]];

function timeLabel(seconds) {
  const time = Math.floor(seconds);
  return `${Math.floor(time / 60)}:${String(time % 60).padStart(2, "0")}`;
}

export default function ThumbnailEditor({ clip, index, ratio, onSave, onClose }) {
  const dialog = useRef(null);
  const title = useId();
  const headline = useId();
  const directionLabel = useId();
  const [draft, setDraft] = useState(clip.thumbnail_design || startingDesign);
  const [direction, setDirection] = useState("");
  const [preview, setPreview] = useState("");
  const [changed, setChanged] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const choices = clip.thumbnail_options || [];
  const selected = choices[draft.frame];
  const generated = clip.thumbnail_source === "ai";

  useEffect(() => {
    const modal = dialog.current;
    modal.showModal();
    return () => modal.close();
  }, []);

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  function change(next) {
    setDraft((current) => ({ ...current, ...next }));
    setChanged(true);
    setPreview("");
    setError("");
    setNotice("");
  }

  async function run(action) {
    if (busy) return;
    setBusy(action);
    setError("");
    setNotice("");
    try {
      if (action === "generate") {
        const render = await submit(clip.thumbnail_generate_url, { direction });
        onSave(render);
        setDraft(render.clips[index].thumbnail_design || startingDesign);
        setChanged(false);
        setPreview("");
        setNotice("AI thumbnail saved.");
      } else if (action === "preview") {
        const image = await submit(clip.thumbnail_preview_url, draft, "blob");
        setPreview(URL.createObjectURL(image));
      } else if (action === "frames") {
        const render = await submit(clip.thumbnail_editor_url, {});
        onSave(render);
        change(render.clips[index].thumbnail_design || startingDesign);
      } else {
        onSave(await submit(clip.thumbnail_save_url, draft));
        onClose();
      }
    } catch (failure) {
      setError(Object.values(failure.fields || {})[0]?.[0]?.message || failure.message);
    } finally {
      setBusy("");
    }
  }

  const image = preview || (changed && selected?.url) || clip.thumbnail_url;
  return (
    <dialog className="thumbnail-dialog" ref={dialog} aria-labelledby={title}
      onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
      <header>
        <div>
          <h2 id={title}>Edit thumbnail</h2>
          <p>{clip.title}</p>
        </div>
        <button type="button" className="icon-button thumbnail-close" onClick={onClose}
          disabled={Boolean(busy)} aria-label="Close thumbnail editor"><X size={20} aria-hidden="true" /></button>
      </header>
      <div className="thumbnail-editor-grid">
        <div className="thumbnail-stage">
          <div className={`thumbnail-preview thumbnail-ratio-${ratio.replace(":", "-")}`}>
            <Image src={image} alt={preview ? "Rendered thumbnail design" : changed ? "Selected video frame" : "Saved thumbnail"}
              fill unoptimized sizes="(max-width: 650px) 84vw, 420px" />
          </div>
          <span className="muted">{preview ? "Export preview" : changed && selected ? `Frame at ${timeLabel(selected.time)}` :
            generated ? `AI cover · ${clip.thumbnail_model}` : "Saved cover"}</span>
        </div>
        <div className="thumbnail-controls">
          {clip.thumbnail_generate_url && <form className="thumbnail-ai" onSubmit={(event) => { event.preventDefault(); run("generate"); }}>
            <label htmlFor={directionLabel}>Art direction <small>(optional)</small></label>
            <div className="input-wrap"><textarea id={directionLabel} rows={2} value={direction} maxLength={420}
              placeholder="Subject, mood, or colors to emphasize" disabled={Boolean(busy)}
              onChange={(event) => { setDirection(event.target.value); setNotice(""); setError(""); }} /></div>
            <div className="thumbnail-ai-actions">
              <button type="submit" className="primary-button" disabled={Boolean(busy)}>
                {generated ? <RefreshCw size={16} aria-hidden="true" /> : <Sparkles size={16} aria-hidden="true" />}
                {busy === "generate" ? "Generating…" : generated ? "Regenerate thumbnail" : "Generate AI thumbnail"}
              </button>
              <a href="/settings#thumbnail-ai-settings-title">Image model settings</a>
            </div>
            <small className="muted">Each successful generation saves a new cover for downloads and future publications.</small>
          </form>}
          {error && <p className="field-error" role="alert">{error}</p>}
          {(notice || busy === "generate") && <p className="muted" role="status">
            {busy === "generate" ? "Waiting for the image model…" : notice}</p>}
          <details className="thumbnail-frame-tools">
            <summary>Design from a video frame</summary>
            <form onSubmit={(event) => { event.preventDefault(); run("save"); }}>
          {choices.length ? (
            <fieldset className="control-group" disabled={Boolean(busy)}>
              <legend>Choose a frame</legend>
              <div className="thumbnail-choices">
                {choices.map(({ url, time }, choice) => (
                  <button key={url} type="button" aria-pressed={draft.frame === choice}
                    onClick={() => change({ frame: choice })} aria-label={`Frame ${choice + 1} at ${timeLabel(time)}`}>
                    <span><Image src={url} alt="" fill unoptimized sizes="110px" /></span>
                    <small>{timeLabel(time)}</small>
                  </button>
                ))}
              </div>
              <label htmlFor={headline}>Headline <small>(optional)</small></label>
              <div className="input-wrap">
                <input id={headline} value={draft.headline} maxLength={52} placeholder="Keep it short and specific"
                  onChange={(event) => change({ headline: event.target.value })} />
              </div>
              <div className="options thumbnail-design-options">
                <SelectField label="Text color" value={draft.accent} choices={accents}
                  onChange={(accent) => change({ accent })} />
                <SelectField label="Text placement" value={draft.placement} choices={placements}
                  onChange={(placement) => change({ placement })} />
              </div>
            </fieldset>
          ) : (
            <div className="thumbnail-find">
              <p className="muted">Find frame choices in this saved video. Its burned-in captions may appear in the stills.</p>
              <button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => run("frames")}>
                <ImageIcon size={16} aria-hidden="true" /> {busy === "frames" ? "Finding frames…" : "Find frames"}
              </button>
            </div>
          )}
          {selected && <div className="thumbnail-actions">
            <a className="secondary-button" href={`${selected.url}?download=1`}>
              <Download size={16} aria-hidden="true" /> Save frame JPG
            </a>
            <button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => run("preview")}>
              {busy === "preview" ? "Rendering…" : "Preview cover"}
            </button>
            <button type="submit" className="primary-button" disabled={Boolean(busy)}>
              <Save size={16} aria-hidden="true" /> {busy === "save" ? "Saving…" : "Use thumbnail"}
            </button>
          </div>}
            </form>
          </details>
        </div>
      </div>
    </dialog>
  );
}
