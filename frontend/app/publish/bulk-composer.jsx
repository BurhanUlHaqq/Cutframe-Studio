"use client";

import { useId, useRef, useState } from "react";
import { CalendarClock, Send, X } from "lucide-react";
import { submit } from "../api";
import { SelectField } from "../render-options";
import MediaBrowser from "./media-browser";
import { platforms } from "./platforms";

const clipLimit = 50;
const batchLimit = 150;
const initialOptions = { privacy: "private", made_for_kids: "", synthetic: false, start: "", spacing: "0", confirm: false };
const visibilities = [["private", "Private"], ["unlisted", "Unlisted"], ["public", "Public"]];
const audiences = [["", "Choose audience"], ["false", "No"], ["true", "Yes"]];
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

function targetKey(target) {
  return `${target.platform}:${target.id}`;
}

export default function BulkComposer({ jobs, loadError, onRetry, destinations, initialJob, onAccounts, onQueue, setParentBusy }) {
  const field = useId();
  const [selection, setSelection] = useState({});
  const [targets, setTargets] = useState([]);
  const [options, setOptions] = useState(initialOptions);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef(null);

  const chosen = Object.entries(selection);
  const pickedTargets = destinations.filter((target) => targets.includes(targetKey(target)));
  const postCount = pickedTargets.length * chosen.length;
  const maxClips = Math.min(clipLimit, Math.floor(batchLimit / Math.max(1, pickedTargets.length)));
  const youtube = pickedTargets.some((target) => target.platform === "youtube");
  const tiktok = pickedTargets.some((target) => target.platform === "tiktok");
  const instagram = pickedTargets.some((target) => target.platform === "instagram");
  const spacing = Number(options.spacing) || 0;
  const tiktokLimited = tiktok && chosen.length > 5 && spacing * 5 < 1440;
  const captionLimit = instagram ? 2200 : 5000;
  const start = options.start && new Date(options.start);
  const validStart = start && Number.isFinite(start.getTime());
  const issue = (postCount > batchLimit && `Choose up to ${batchLimit} posts per batch. Each destination counts separately.`) ||
    (tiktokLimited && "Choose up to five TikTok drafts per 24 hours. Extend the send gap or select fewer clips.");

  function markChanged() {
    requestId.current = null;
    setError("");
    setOptions((current) => ({ ...current, confirm: false }));
  }

  function change(values) {
    markChanged();
    setOptions((current) => ({ ...current, ...values }));
  }

  function add(items) {
    if (!items.length || chosen.length >= maxClips) return;
    markChanged();
    const next = { ...selection };
    for (const { key, job, index, clip } of items.slice(0, maxClips - chosen.length)) {
      next[key] = { job_id: job.id, clip_index: index, title: clip.title.slice(0, 100),
                    caption: clip.description, original: clip.title };
    }
    setSelection(next);
  }

  function remove(key) {
    markChanged();
    setSelection((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== key)));
  }

  function changeCopy(key, fields) {
    markChanged();
    setSelection((current) => ({ ...current, [key]: { ...current[key], ...fields } }));
  }

  function toggleTarget(key, checked) {
    markChanged();
    setTargets((current) => checked ? [...current, key] : current.filter((value) => value !== key));
  }

  async function queue(event) {
    event.preventDefault();
    if (!postCount || !options.confirm || busy || issue || (youtube && options.made_for_kids === "")) return;
    setBusy(true);
    setParentBusy(true);
    setError("");
    try {
      requestId.current ||= crypto.randomUUID();
      const result = await submit("publishing/posts/bulk/", {
        request_id: requestId.current, confirm: true,
        clips: chosen.map(([, { job_id, clip_index, title, caption }]) => ({ job_id, clip_index, title, caption })),
        destinations: pickedTargets.map(({ platform, id }) => ({ platform, account_id: id })),
        privacy: youtube ? options.privacy : "", made_for_kids: youtube ? options.made_for_kids : "",
        synthetic: options.synthetic, scheduled_at: validStart ? start.toISOString() : null, spacing_minutes: spacing,
      });
      onQueue(result, Boolean(validStart || spacing));
    } catch (failure) {
      setError(Object.values(failure.fields || {})[0]?.[0]?.message || failure.message);
    } finally {
      setBusy(false);
      setParentBusy(false);
    }
  }

  return (
    <form className="publish-bulk-grid" onSubmit={queue}>
      <MediaBrowser jobs={jobs} loadError={loadError} onRetry={onRetry} initialJob={initialJob} checked={selection}
        disabled={busy} limit={maxClips} onToggle={(item, checked) => checked ? add([item]) : remove(item.key)}
        onSelectShown={add} onClear={() => { markChanged(); setSelection({}); }} />

      <section className="panel settings-panel" aria-labelledby={`${field}-review`}>
        <div className="panel-heading">
          <span className="heading-icon"><CalendarClock size={21} aria-hidden="true" /></span>
          <div><h2 id={`${field}-review`}>Review and queue</h2><p>{chosen.length} {chosen.length === 1 ? "video" : "videos"} · {pickedTargets.length} {pickedTargets.length === 1 ? "account" : "accounts"} · {postCount} {postCount === 1 ? "post" : "posts"}</p></div>
        </div>
        <fieldset className="control-group publish-fields" disabled={busy}>
          <legend className="sr-only">Batch publishing options</legend>
          <fieldset className="control-group bulk-targets">
            <legend>Destination accounts</legend>
            {!destinations.length && <div className="publish-connect-hint">Connect an account to send the batch.
              <div><button className="secondary-button" type="button" onClick={onAccounts}>Open accounts</button></div></div>}
            {destinations.map((target) => {
              const key = targetKey(target);
              const { Icon, name } = platforms[target.platform];
              return <label className="bulk-target" key={key}>
                <input type="checkbox" checked={targets.includes(key)} onChange={(event) => toggleTarget(key, event.target.checked)} />
                <Icon size={18} aria-hidden="true" /><span><strong>{target.label}</strong><small>{name}</small></span>
              </label>;
            })}
            {tiktok && <small>TikTok sends inbox drafts. Open each inbox notification in TikTok to finish posting.</small>}
          </fieldset>
          {youtube && <div className="publish-options">
            <SelectField label="YouTube visibility" value={options.privacy} choices={visibilities} onChange={(privacy) => change({ privacy })} />
            <SelectField label="Made for kids" value={options.made_for_kids} choices={audiences} onChange={(made_for_kids) => change({ made_for_kids })} />
          </div>}
          {(youtube || instagram) && <label className="publish-check">
            <input type="checkbox" checked={options.synthetic} onChange={(event) => change({ synthetic: event.target.checked })} />
            Disclose AI-generated or materially altered media for these videos
          </label>}
          <div className="publish-options">
            <div><label htmlFor={`${field}-start`}>First send time</label>
              <div className="input-wrap"><input id={`${field}-start`} type="datetime-local" value={options.start}
                onChange={(event) => change({ start: event.target.value })} /></div>
              <small>Uses your browser’s time zone. Blank starts as soon as the worker is ready.</small>
            </div>
            <div><label htmlFor={`${field}-spacing`}>Schedule gap in minutes</label>
              <div className="input-wrap"><input id={`${field}-spacing`} type="number" min={0} max={10080} step={1} value={options.spacing}
                onChange={(event) => change({ spacing: event.target.value })} /></div>
              <small>Each video and its destinations share the same scheduled time.</small>
            </div>
          </div>
          {chosen.length > 0 && <div className="bulk-copy">
            <h3>Post text</h3>
            {pickedTargets.length > 1 && <small>Each video uses the same title and caption across its selected accounts.</small>}
            <ol>{chosen.map(([key, row], index) => (
              <li className="bulk-copy-row" key={key}>
                <details>
                  <summary><span><strong>{row.title || row.original}</strong><small>{validStart ? (
                    dateFormat.format(new Date(start.getTime() + index * spacing * 60_000))
                  ) : spacing && index ? `${index * spacing} min after the first video` : "Send when ready"}</small></span></summary>
                  <label htmlFor={`${field}-${key}-title`}>Title</label>
                  <div className="input-wrap"><input id={`${field}-${key}-title`} required maxLength={100} value={row.title}
                    onChange={(event) => changeCopy(key, { title: event.target.value })} /></div>
                  <label htmlFor={`${field}-${key}-caption`}>Description or caption</label>
                  <textarea id={`${field}-${key}-caption`} rows={3} maxLength={captionLimit} value={row.caption}
                    onChange={(event) => changeCopy(key, { caption: event.target.value })} />
                </details>
                <button type="button" className="secondary-button bulk-remove" aria-label={`Remove ${row.original}`} onClick={() => remove(key)}>
                  <X size={16} aria-hidden="true" /></button>
              </li>
            ))}</ol>
          </div>}
          {(issue || error) && <p className="field-error" role="alert">{issue || error}</p>}
          {postCount > 0 && <label className="publish-check">
            <input type="checkbox" required checked={options.confirm} onChange={(event) => change({ confirm: event.target.checked })} />
            <span>I approve {postCount} {postCount === 1 ? "delivery" : "deliveries"} to {pickedTargets.map(({ label, platform }) => `${label} on ${platforms[platform].name}`).join(", ")}.</span>
          </label>}
        </fieldset>
        <div className="settings-actions bulk-footer">
          <button type="submit" className="primary-button" disabled={busy || !postCount || !options.confirm || Boolean(issue) ||
            (youtube && !options.made_for_kids) || chosen.some(([, row]) => !row.title.trim())}>
            <Send size={17} aria-hidden="true" /> {busy ? "Queueing batch…" : `Queue ${postCount || ""} ${postCount === 1 ? "post" : "posts"}`}
          </button>
        </div>
      </section>
    </form>
  );
}
