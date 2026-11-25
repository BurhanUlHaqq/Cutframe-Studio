"use client";

import { useEffect, useId, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import {
  AlertCircle, Archive, Captions, Check, Clapperboard, Copy, Download, FileText,
  Film, Image as ImageIcon, List, LoaderCircle, Pencil, Play, Plus, RotateCcw,
} from "lucide-react";
import { api, stages, statusLabels, submit } from "../../api";
import { usePreferences } from "../../preferences";

const terminal = new Set(["complete", "failed"]);
const eventTime = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" });

function formatTime(seconds) {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function RenderActivity({ events = [], active }) {
  if (!active && !events.length) return null;
  return (
    <section className="render-activity" aria-labelledby="render-activity-title">
      <h2 id="render-activity-title"><List size={18} aria-hidden="true" /> Render activity</h2>
      {events.length ? (
        <ol className="activity-list" tabIndex={0} aria-label="Recent render events">
          {events.slice().reverse().map((event, index) => (
            <li className={`activity-${event.level}`} key={`${event.time}-${index}`}>
              <time dateTime={event.time}>{eventTime.format(new Date(event.time))}</time>
              <span>{event.message}</span>
            </li>
          ))}
        </ol>
      ) : <p className="muted">Waiting for the first worker update.</p>}
    </section>
  );
}

function ClipCard({ clip, ratio, onSave, showScores }) {
  const [playing, setPlaying] = useState(false);
  const [copyState, setCopyState] = useState("");
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState("");
  const editId = useId();
  const formattedRatio = ratio.replace(":", " / ");
  async function copy(titleOnly = false) {
    try {
      await navigator.clipboard.writeText(titleOnly ? clip.title : `${clip.title}\n\n${clip.description}`);
      setCopyState(titleOnly ? "Title copied" : "Copied");
    } catch {
      setCopyState("Use the TXT download to copy the title and description.");
    }
  }

  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setEditError("");
    try {
      const updated = await submit(clip.edit_url, draft);
      onSave(updated);
      setDraft(null);
      setCopyState("");
    } catch (error) {
      setEditError(Object.values(error.fields || {})[0]?.[0]?.message || error.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <article className="panel clip-card">
      <div className="clip-player" style={{ "--clip-ratio": formattedRatio }}>
        {playing ? (
          <video controls autoPlay playsInline preload="metadata" src={clip.video_url}
            poster={clip.thumbnail_url} aria-label={clip.title} />
        ) : (
          <button type="button" className="clip-poster" onClick={() => setPlaying(true)} aria-label={`Play ${clip.title}`}>
            <Image src={clip.thumbnail_url} alt="" fill unoptimized sizes="(max-width: 600px) 90vw, 370px" />
            <span className="play-icon"><Play size={20} fill="currentColor" aria-hidden="true" /></span>
          </button>
        )}
      </div>
      <div className="clip-copy">
        <div className="clip-meta">
          <span>{formatTime(clip.start_time)}–{formatTime(clip.end_time)} · {Number((clip.end_time - clip.start_time).toFixed(1))}s</span>
          {showScores && <span className="clip-score">Score {clip.score}</span>}
        </div>
        {draft ? (
          <form className="clip-editor" onSubmit={save}>
            <label htmlFor={`${editId}-title`}>Title</label>
            <input id={`${editId}-title`} value={draft.title} required maxLength={100}
              onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
            <label htmlFor={`${editId}-description`}>Description</label>
            <textarea id={`${editId}-description`} value={draft.description} required maxLength={1200} rows={3}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
            {editError && <p className="field-error" role="alert">{editError}</p>}
            <div className="clip-actions">
              <button className="primary-button" disabled={saving}>{saving ? "Saving…" : "Save"}</button>
              <button className="secondary-button" type="button" disabled={saving} onClick={() => setDraft(null)}>Cancel</button>
            </div>
          </form>
        ) : (
          <>
            <h3>{clip.title}</h3>
            <p>{clip.description}</p>
            <div className="clip-actions">
              <button className="secondary-button" type="button" onClick={() => {
                setDraft({ title: clip.title, description: clip.description });
                setEditError("");
              }}><Pencil size={16} aria-hidden="true" /> Edit</button>
              <button className="secondary-button" type="button" onClick={() => copy(true)}>
                {copyState === "Title copied" ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
                {copyState === "Title copied" ? "Title copied" : "Copy title"}
              </button>
              <button className="secondary-button" type="button" onClick={() => copy()}>
                {copyState === "Copied" ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
                {copyState === "Copied" ? "Copied" : "Copy text"}
              </button>
              <a className="secondary-button" href={`${clip.video_url}?download=1`} aria-label={`Download MP4 for ${clip.title}`}>
                <Download size={16} aria-hidden="true" /> MP4
              </a>
              <a className="secondary-button" href={clip.text_url}><FileText size={16} aria-hidden="true" /> TXT</a>
              <a className="secondary-button" href={`${clip.thumbnail_url}?download=1`} aria-label={`Download thumbnail for ${clip.title}`}>
                <ImageIcon size={16} aria-hidden="true" /> JPG
              </a>
              {clip.captions_url && <a className="secondary-button" href={clip.captions_url} aria-label={`Download captions for ${clip.title}`}>
                <Captions size={16} aria-hidden="true" /> SRT
              </a>}
            </div>
          </>
        )}
        <span className="sr-only" aria-live="polite">{copyState}</span>
      </div>
    </article>
  );
}

export default function RenderDetails({ id }) {
  const { preferences, loading: preferencesLoading } = usePreferences();
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    let timer;
    async function poll() {
      try {
        const data = await api(`jobs/${id}/`, { signal: abort.signal });
        if (abort.signal.aborted) return;
        setJob(data);
        setError("");
        if (!terminal.has(data.status)) timer = setTimeout(poll, 1700);
      } catch (failure) {
        if (abort.signal.aborted) return;
        setError(failure.message);
        if (failure.status !== 404) timer = setTimeout(poll, 4500);
      }
    }
    poll();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [id, refresh]);

  async function retry() {
    setRetrying(true);
    try {
      const data = await submit(`jobs/${id}/retry/`, {});
      setJob(data);
      setError("");
      setRefresh((value) => value + 1);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setRetrying(false);
    }
  }

  if (!job) return (
    <div className="panel render-panel" role={error ? "alert" : "status"}>
      <h1>{error ? "Render unavailable" : "Loading render…"}</h1>
      {error && <p>{error}</p>}
      {error && <Link href="/" className="secondary-button">Back to studio</Link>}
    </div>
  );

  const complete = job.status === "complete";
  const failed = job.status === "failed";
  const byScore = job.selection_mode === "score";
  const count = complete ? job.clips.length : job.clip_count;
  const noMatches = complete && count === 0;
  const countLabel = `${count} ${count === 1 ? "clip" : "clips"}`;
  const selection = byScore ? `Viral score ${job.min_viral_score}+` : `Up to ${countLabel}`;
  const activeStage = stages.findIndex(([key]) => key === job.stage);
  const renderStages = job.source_type === "upload"
    ? [["download", "Read uploaded video"], ...stages.slice(1)] : stages;
  const activeLabel = renderStages[activeStage]?.[1];
  return (
    <>
      <section className="panel render-panel">
        <div className="render-head">
          <div className="render-title">
            <span className="heading-icon">{complete ? <Archive size={23} aria-hidden="true" /> : <Clapperboard size={23} aria-hidden="true" />}</span>
            <div>
              <h1>{noMatches ? "No clips met your score" : complete ? "Your clips are ready" : failed ? "Render failed" : "Creating your clips"}</h1>
              <p>{complete ? countLabel : selection} · {job.aspect_ratio} · {job.source_name || `${job.quality}p`}</p>
            </div>
          </div>
          {noMatches ? <Link className="secondary-button" href="/"><Plus size={17} aria-hidden="true" /> New render</Link> :
            complete ? <a className="primary-button" href={job.archive_url}><Download size={18} aria-hidden="true" /> Download ZIP</a> :
            <span className={`status ${job.status}`}>{statusLabels[job.status]}</span>}
        </div>
        {error && <p className="field-error" role="alert">{error}</p>}
        {failed ? (
          <div className="failure-box">
            <AlertCircle size={21} aria-hidden="true" />
            <p>{job.error}</p>
            <button className="secondary-button" type="button" onClick={retry} disabled={retrying}>
              <RotateCcw size={16} aria-hidden="true" /> {retrying ? "Queuing…" : "Retry render"}
            </button>
          </div>
        ) : !complete ? (
          <div className="render-progress">
            <div className="progress-label" role="status">
              <span>{job.activity || (job.status === "queued" ? "Waiting for the render worker" : activeLabel || "Starting render")}</span>
              <strong title="Estimated overall progress">{job.progress}%</strong>
            </div>
            <progress max={100} value={job.progress} aria-label="Estimated overall render progress" />
            <ol className="stage-list">
              {renderStages.map(([key, label], index) => (
                <li key={key} className={index < activeStage ? "done" : index === activeStage ? "active" : ""}>
                  {index < activeStage ? <Check size={15} aria-hidden="true" /> :
                    index === activeStage ? <LoaderCircle size={15} className="spin" aria-hidden="true" /> : <span className="stage-dot" aria-hidden="true" />}
                  {label}
                </li>
              ))}
            </ol>
            <p className="muted">You can close this page while the worker finishes. Find the result in Render history.</p>
          </div>
        ) : noMatches ? (
          <p className="muted">No highlight reached {job.min_viral_score}. A lower minimum may return more clips.</p>
        ) : !byScore && count < job.clip_count ? (
          <p className="muted">This render produced {count} of the {job.clip_count} requested clips.</p>
        ) : null}
        <RenderActivity events={job.events} active={!complete && !failed} />
      </section>
      {complete && !noMatches && (
        <section className="render-results" aria-labelledby="clip-list-title">
          <div className="section-heading">
            <h2 id="clip-list-title"><Film size={20} aria-hidden="true" /> Review clips</h2>
            <Link className="secondary-button" href="/"><Plus size={17} aria-hidden="true" /> New render</Link>
          </div>
          <div className="clip-grid">
            {job.clips.map((clip) => <ClipCard key={clip.video_url} clip={clip} ratio={job.aspect_ratio}
              onSave={setJob} showScores={!preferencesLoading && preferences.show_scores} />)}
          </div>
        </section>
      )}
    </>
  );
}
