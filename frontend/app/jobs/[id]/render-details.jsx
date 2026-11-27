"use client";

import { useEffect, useId, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import {
  AlertCircle, Archive, Captions, Check, ChevronDown, Clapperboard, Copy, Download, Ellipsis, FileText,
  Film, Image as ImageIcon, List, LoaderCircle, Pencil, Play, Plus, RotateCcw, Send,
} from "lucide-react";
import { api, stages, statusLabels, submit } from "../../api";
import { usePreferences } from "../../preferences";
import { editingStyles, SelectField } from "../../render-options";
import ThumbnailEditor from "./thumbnail-editor";
import { formatSourceTimestamp } from "../../source-range-options";

const terminal = new Set(["complete", "failed", "cancelled", "deleting"]);
const descriptionTargets = [["social", "General social"], ["youtube", "YouTube"], ["instagram", "Instagram Reels"], ["tiktok", "TikTok"]];
const eventTime = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" });

function formatTime(seconds) {
  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor(whole / 60) % 60;
  return `${hours ? `${hours}:${String(minutes).padStart(2, "0")}` : minutes}:${String(whole % 60).padStart(2, "0")}`;
}

function RenderActivity({ events = [], active, failed }) {
  if (!active && !events.length) return null;
  return (
    <details className="render-activity" open={active || failed}>
      <summary><List size={18} aria-hidden="true" /> Render activity <ChevronDown size={15} aria-hidden="true" /></summary>
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
    </details>
  );
}

function ClipCard({ clip, index, ratio, onSave, onPreviewPlay, showScores, publishUrl, reel, gameplay }) {
  const [playing, setPlaying] = useState(false);
  const [copyState, setCopyState] = useState("");
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [writing, setWriting] = useState(false);
  const [platform, setPlatform] = useState("social");
  const [draftModel, setDraftModel] = useState("");
  const [editError, setEditError] = useState("");
  const [editingCover, setEditingCover] = useState(false);
  const editId = useId();
  const video = useRef(null);
  const editTrigger = useRef(null);
  const seek = useRef(0);
  const formattedRatio = ratio.replace(":", " / ");
  async function copy(titleOnly = false) {
    try {
      await navigator.clipboard.writeText(titleOnly ? clip.title : [clip.title, clip.description, clip.chapter_text].filter(Boolean).join("\n\n"));
      setCopyState(titleOnly ? "Title copied." : "Post text copied.");
    } catch {
      setCopyState("Use the TXT download to copy the title and description.");
    }
  }

  function closeEditor() {
    setDraft(null);
    requestAnimationFrame(() => editTrigger.current?.focus());
  }

  function playChapter(time) {
    seek.current = time;
    if (video.current?.readyState) {
      video.current.currentTime = time;
      video.current.play().catch(() => {});
    }
    setPlaying(true);
  }

  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setEditError("");
    try {
      const updated = await submit(clip.edit_url, draft);
      onSave(updated);
      closeEditor();
      setCopyState("");
    } catch (error) {
      setEditError(Object.values(error.fields || {})[0]?.[0]?.message || error.message);
    } finally {
      setSaving(false);
    }
  }

  async function regenerateDescription() {
    if (saving || writing) return;
    setWriting(true);
    setEditError("");
    setDraftModel("");
    try {
      const result = await submit(clip.description_draft_url, { ...draft, platform });
      setDraft((previous) => ({ ...previous, description: result.description }));
      setDraftModel(result.model);
    } catch (failure) {
      setEditError(Object.values(failure.fields || {})[0]?.[0]?.message || failure.message);
    } finally {
      setWriting(false);
    }
  }

  return (
    <article className="panel clip-card">
      <div className="clip-player" style={{ "--clip-ratio": formattedRatio }}>
        {playing ? (
          <video ref={video} onPlay={onPreviewPlay} controls autoPlay playsInline preload="metadata" src={clip.video_url}
            onLoadedMetadata={(event) => { if (seek.current) event.currentTarget.currentTime = seek.current; }}
            poster={clip.thumbnail_url} aria-label={clip.title} />
        ) : (
          <button type="button" className="clip-poster" onClick={() => setPlaying(true)} aria-label={`Play ${clip.title}`}>
            <Image src={clip.thumbnail_url} alt="" fill unoptimized sizes={reel ? "(max-width: 790px) 90vw, 720px" : "(max-width: 600px) 90vw, 370px"} />
            <span className="play-icon"><Play size={20} fill="currentColor" aria-hidden="true" /></span>
          </button>
        )}
      </div>
      <div className="clip-copy">
        <div className="clip-meta">
          <span>{reel ? `${formatTime(clip.end_time)} runtime · ${clip.chapters?.length ?? 0} selected moments` :
            `${formatTime(clip.start_time)}–${formatTime(clip.end_time)} · ${Number((clip.duration_seconds ?? clip.end_time - clip.start_time).toFixed(1))}s`}</span>
          {showScores && typeof clip.score === "number" && <span className="clip-score">{gameplay ? "Activity" : "Score"} {clip.score}</span>}
        </div>
        {draft ? (
          <form className="clip-editor" onSubmit={save}>
            <label htmlFor={`${editId}-title`}>Title</label>
            <input id={`${editId}-title`} value={draft.title} required maxLength={100} autoFocus disabled={saving || writing}
              onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
            <label htmlFor={`${editId}-description`}>Description</label>
            <textarea id={`${editId}-description`} value={draft.description} required maxLength={1200} rows={5} disabled={saving || writing}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
            {clip.description_draft_url && <fieldset className="control-group description-draft" disabled={saving || writing}>
              <legend className="sr-only">AI description draft</legend>
              <SelectField label="Description for" value={platform} choices={descriptionTargets} onChange={setPlatform} />
              <div>
                <button className="secondary-button" type="button" onClick={regenerateDescription}>
                  <RotateCcw size={15} aria-hidden="true" /> {writing ? "Writing description…" : "Regenerate description"}
                </button>
                <Link href="/settings#description-settings-title">Change model</Link>
              </div>
            </fieldset>}
            {(draftModel || writing) && <p className="muted description-draft-note" role="status">{writing ?
              "Waiting for the description model…" : `Draft from ${draftModel}. Review it before saving.`}</p>}
            {editError && <p className="field-error" role="alert">{editError}</p>}
            <div className="clip-actions">
              <button className="primary-button" disabled={saving || writing}>{saving ? "Saving…" : "Save"}</button>
              <button className="secondary-button" type="button" disabled={saving || writing} onClick={closeEditor}>Cancel</button>
            </div>
          </form>
        ) : (
          <>
            <h3>{clip.title}</h3>
            <p>{clip.description}</p>
            {reel && clip.chapters?.length > 0 && <details className="advanced reel-chapters">
              <summary>Chapters</summary>
              <ol>{clip.chapters.map((chapter) => (
                <li key={chapter.time}><button className="chapter-link" type="button" onClick={() => playChapter(chapter.time)}>
                  <span>{formatTime(chapter.time)}</span><strong>{chapter.title}</strong>
                  <small>Source {formatTime(chapter.source_time)}</small>
                </button></li>
              ))}</ol>
            </details>}
            <div className="clip-actions clip-key-actions">
              <Link className="secondary-button clip-publish" href={publishUrl} aria-label={`Publish ${clip.title}`}>
                <Send size={16} aria-hidden="true" /> Publish
              </Link>
              <a className="secondary-button" href={`${clip.video_url}?download=1`} aria-label={`Download MP4 for ${clip.title}`}>
                <Download size={16} aria-hidden="true" /> MP4
              </a>
              <button ref={editTrigger} className="secondary-button" type="button" onClick={() => {
                setDraft({ title: clip.title, description: clip.description });
                setEditError("");
                setDraftModel("");
              }}><Pencil size={16} aria-hidden="true" /> Edit text</button>
            </div>
            <details className="clip-tools">
              <summary><Ellipsis size={17} aria-hidden="true" /> More clip actions <ChevronDown size={15} aria-hidden="true" /></summary>
              <div className="clip-actions">
                {clip.thumbnail_editor_url && <button className="secondary-button" type="button" onClick={() => setEditingCover(true)}>
                  <ImageIcon size={16} aria-hidden="true" /> Edit thumbnail
                </button>}
                <button className="secondary-button" type="button" onClick={() => copy(true)}><Copy size={16} aria-hidden="true" /> Copy title</button>
                <button className="secondary-button" type="button" onClick={() => copy()}><Copy size={16} aria-hidden="true" /> Copy post text</button>
                <a className="secondary-button" href={clip.text_url} aria-label={`Download post text for ${clip.title}`}>
                  <FileText size={16} aria-hidden="true" /> TXT</a>
                <a className="secondary-button" href={`${clip.thumbnail_url}${clip.thumbnail_url.includes("?") ? "&" : "?"}download=1`}
                  aria-label={`Download thumbnail for ${clip.title}`}><ImageIcon size={16} aria-hidden="true" /> JPG</a>
                {clip.captions_url && <a className="secondary-button" href={clip.captions_url} aria-label={`Download captions for ${clip.title}`}>
                  <Captions size={16} aria-hidden="true" /> SRT</a>}
              </div>
            </details>
          </>
        )}
        <p className="clip-copy-feedback" role="status">{copyState}</p>
      </div>
      {editingCover && <ThumbnailEditor clip={clip} index={index} ratio={ratio} onSave={onSave}
        onClose={() => setEditingCover(false)} />}
    </article>
  );
}

export default function RenderDetails({ id }) {
  const { preferences, loading: preferencesLoading } = usePreferences();
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [renderAction, setRenderAction] = useState("");
  const [refresh, setRefresh] = useState(0);
  const recentPreview = useRef(null);

  function playPreview(event) {
    if (recentPreview.current !== event.currentTarget) recentPreview.current?.pause();
    recentPreview.current = event.currentTarget;
  }

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

  async function changeRender(action) {
    if (action === "cancel" && !window.confirm("Cancel this render? You can run it again from the Library.")) return;
    setRenderAction(action);
    try {
      const data = await submit(`jobs/${id}/${action}/`, {});
      setJob(data);
      setError("");
      setRefresh((value) => value + 1);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setRenderAction("");
    }
  }

  if (!job) return (
    <div className="panel render-panel" role={error ? "alert" : "status"}>
      <h1>{error ? "Render unavailable" : "Loading render…"}</h1>
      {error && <p>{error}</p>}
      {error && <Link href="/library" className="secondary-button">Back to library</Link>}
    </div>
  );

  const complete = job.status === "complete";
  const failed = job.status === "failed";
  const cancelled = job.status === "cancelled";
  const deleting = job.status === "deleting";
  const canCancel = job.status === "queued" || job.status === "running";
  const reel = job.studio_mode === "highlights";
  const gameplay = job.editing_style === "gameplay";
  const byScore = job.selection_mode === "score";
  const count = complete ? job.clips.length : job.clip_count;
  const noMatches = complete && count === 0;
  const countLabel = `${count} ${count === 1 ? "clip" : "clips"}`;
  const selection = reel ? `Highlights · up to ${job.highlight_minutes} min` :
    byScore ? `${gameplay ? "Activity" : "Viral"} score ${job.min_viral_score}+` : `Up to ${countLabel}`;
  const activeStage = stages.findIndex(([key]) => key === (job.stage === "covers" ? "export" : job.stage));
  const sourceStages = job.source_type === "upload"
    ? [["download", "Read uploaded video"], ...stages.slice(1)] : stages;
  const renderStages = reel ? sourceStages.map(([key, label]) => [key, {
    rank: "Select moments", render: "Assemble reel",
  }[key] || label]) : sourceStages;
  const activeLabel = renderStages[activeStage]?.[1];
  return (
    <>
      <section className="panel render-panel">
        <div className="render-head">
          <div className="render-title">
            <span className="heading-icon">{complete ? <Archive size={23} aria-hidden="true" /> : <Clapperboard size={23} aria-hidden="true" />}</span>
            <div>
              <h1>{cancelled ? "Render cancelled" : deleting ? "Cleanup incomplete" : failed ? "Render failed" : noMatches ? "No clips met your score" :
                complete ? reel ? "Your highlight reel is ready" : "Your clips are ready" :
                reel ? "Creating your highlight reel" : "Creating your clips"}</h1>
              <p>{complete ? reel ? `${formatTime(job.clips[0]?.end_time || 0)} runtime` : countLabel : selection} · {job.aspect_ratio} · {job.source_name || `${job.quality}p`}{!reel && job.editing_style && ` · ${editingStyles[job.editing_style]?.[0]}`}</p>
              {job.source_start_ms != null && job.source_end_ms != null && <p>
                Source range {formatSourceTimestamp(job.source_start_ms)}–{formatSourceTimestamp(job.source_end_ms)}
              </p>}
              {job.source_render_id && <Link className="render-source-link" href={`/jobs/${job.source_render_id}`}>View source render</Link>}
            </div>
          </div>
          <div className="render-result-actions">
            {noMatches ? <Link className="secondary-button" href="/"><Plus size={17} aria-hidden="true" /> New render</Link> :
              complete ? <a className="primary-button" href={job.archive_url}><Download size={18} aria-hidden="true" /> Download ZIP</a> : <>
              <span className={`status ${job.status}`}>{statusLabels[job.status]}</span>
              {canCancel && <button className="secondary-button" type="button" disabled={Boolean(renderAction)}
                onClick={() => changeRender("cancel")}>Cancel render</button>}
              </>}
            {job.can_reuse_source && <Link className="secondary-button" href={`/jobs/${job.id}/alternate`}>
              {reel ? <Film size={17} aria-hidden="true" /> : <Clapperboard size={17} aria-hidden="true" />}
              {reel ? "Create short clips" : "Create a highlight reel"}
            </Link>}
          </div>
        </div>
        {error && <p className="field-error" role="alert">{error}</p>}
        {failed || cancelled ? (
          <div className={failed ? "failure-box" : "render-cancelled"}>
            {failed && <AlertCircle size={21} aria-hidden="true" />}
            <p>{failed ? job.error : job.activity}</p>
            <button className="secondary-button" type="button" onClick={() => changeRender("retry")} disabled={Boolean(renderAction)}>
              <RotateCcw size={16} aria-hidden="true" /> {renderAction === "retry" ? "Queuing…" : cancelled ? "Run again" : "Retry render"}
            </button>
          </div>
        ) : deleting ? <Link className="secondary-button" href="/library?status=deleting">Open Library cleanup</Link> : !complete ? (
          <div className="render-progress">
            <div className="progress-label" role="status">
              <span>{job.activity || (job.status === "queued" ? "Waiting for the render worker" : activeLabel || "Starting render")}</span>
              <strong title="Estimated overall progress">{job.progress}%</strong>
            </div>
            <progress max={100} value={job.progress} aria-label="Estimated overall render progress" />
            {job.status === "running" && job.stage === "covers" && <div className="render-cover-action">
              <button type="button" className="secondary-button" disabled={Boolean(renderAction) || job.skip_ai_covers}
                onClick={() => changeRender("skip-ai-covers")}>
                {job.skip_ai_covers ? "Skipping remaining AI covers…" : "Skip remaining AI covers"}
              </button>
              <small>Finish the ZIP with frame covers for the remaining clips.</small>
            </div>}
            <ol className="stage-list">
              {renderStages.map(([key, label], index) => (
                <li key={key} aria-current={index === activeStage ? "step" : undefined}
                  className={index < activeStage ? "done" : index === activeStage ? "active" : ""}>
                  {index < activeStage ? <Check size={15} aria-hidden="true" /> :
                    index === activeStage ? <LoaderCircle size={15} className="spin" aria-hidden="true" /> : <span className="stage-dot" aria-hidden="true" />}
                  {label}
                </li>
              ))}
            </ol>
            <p className="muted">{job.status === "cancelling" ? "The current operation may take a moment to stop." :
              "You can close this page while the worker finishes. Find the result in the Library."}</p>
          </div>
        ) : noMatches ? (
          <p className="muted">No highlight reached {job.min_viral_score}{gameplay ? " on the activity score" : ""}. A lower minimum may return more clips.</p>
        ) : !reel && !byScore && count < job.clip_count ? (
          <p className="muted">This render produced {count} of the {job.clip_count} requested clips.</p>
        ) : null}
        <RenderActivity events={job.events} active={!terminal.has(job.status)} failed={failed} />
      </section>
      {complete && !noMatches && (
        <section className="render-results" aria-labelledby="clip-list-title">
          <div className="section-heading">
            <h2 id="clip-list-title"><Film size={20} aria-hidden="true" /> {reel ? "Review highlight reel" : "Review clips"}</h2>
            <div className="render-result-actions">
              {count > 1 && <Link className="secondary-button" href={`/publish?job=${job.id}&mode=bulk`}>
                <Send size={16} aria-hidden="true" /> Bulk post
              </Link>}
              <Link className="secondary-button" href="/"><Plus size={17} aria-hidden="true" /> New render</Link>
            </div>
          </div>
          {reel && <p className="muted">
            {job.clips[0]?.highlight_review === "transcript" ? "Automatic screening covered spoken dialogue. "
              : job.clips[0]?.highlight_review === "activity" ? "This reel was selected by visual activity. "
              : "This reel requires a manual review. "}
            Before posting, review the picture, audio, captions, title, description, and cover.
            Confirm you have the rights to use the footage and music.
          </p>}
          <div className={reel ? "reel-result" : "clip-grid"}>
            {job.clips.map((clip, index) => <ClipCard key={clip.video_url} reel={reel} gameplay={gameplay} clip={clip} index={index} ratio={job.aspect_ratio}
              publishUrl={`/publish?job=${job.id}&clip=${index}`}
              onSave={setJob} onPreviewPlay={playPreview} showScores={!preferencesLoading && preferences.show_scores} />)}
          </div>
        </section>
      )}
    </>
  );
}
