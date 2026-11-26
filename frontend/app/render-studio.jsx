"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Archive, ArrowDown, CheckCircle2, Clapperboard, FileVideo, Film, Link2, LibraryBig, Sparkles, Upload, X,
} from "lucide-react";
import { api, statusLabels, submit, submitUpload } from "./api";
import { usePreferences } from "./preferences";
import RenderOptions, { editingStyles, HighlightOptions } from "./render-options";

const modes = [
  ["shorts", Film, "Short Clips", "A pack of standalone clips"],
  ["highlights", LibraryBig, "Highlights", "One landscape reel up to 30 minutes"],
];

function fileSize(bytes) {
  const gb = 1024 ** 3;
  return bytes >= gb ? `${(bytes / gb).toFixed(1).replace(/\.0$/, "")} GB` :
    `${Math.max(0.1, bytes / 1024 ** 2).toFixed(1).replace(/\.0$/, "")} MB`;
}

function outputLabel(options) {
  if (options.studio_mode === "highlights") return options.highlight_minutes ? `Up to ${options.highlight_minutes} min` : "1–30 min";
  if (options.selection_mode === "score") {
    const score = options.editing_style === "gameplay" ? "Activity score" : "Viral score";
    return options.min_viral_score == null ? score : `${score} ${options.min_viral_score}+`;
  }
  return `Up to ${options.clip_count} ${options.clip_count === 1 ? "clip" : "clips"}`;
}

export default function RenderStudio({ reuseId = "" }) {
  const router = useRouter();
  const { preferences, loading: preferencesLoading } = usePreferences();
  const [source, setSource] = useState("");
  const [sourceType, setSourceType] = useState("youtube");
  const [video, setVideo] = useState(null);
  const [dragging, setDragging] = useState(false);
  const sourceInput = useRef(null);
  const videoInput = useRef(null);
  const [upload, setUpload] = useState(null);
  const [uploadPercent, setUploadPercent] = useState(null);
  const [original, setOriginal] = useState(null);
  const [sourceError, setSourceError] = useState("");
  const [sourceAttempt, setSourceAttempt] = useState(0);
  const [overrides, setOverrides] = useState({});
  const { theme, show_scores, ...renderOptions } = { ...preferences, ...(original && {
    studio_mode: original.studio_mode === "highlights" ? "shorts" : "highlights",
    quality: original.quality, language: original.language ?? preferences.language ?? "", caption_style: original.caption_style,
  }), ...overrides };
  const { studio_mode, highlight_minutes, highlight_method, quality, language, caption_style } = renderOptions;
  const highlights = studio_mode === "highlights";
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [history, setHistory] = useState(null);
  const [historyError, setHistoryError] = useState("");

  useEffect(() => {
    if (sending) return;
    if (errors.source_url) sourceInput.current?.focus();
    if (errors.video) videoInput.current?.focus();
  }, [errors, sending]);

  useEffect(() => {
    if (reuseId) return;
    const abort = new AbortController();
    api("jobs/", { signal: abort.signal })
      .then((data) => { setHistory(data.jobs.slice(0, 5)); setUpload(data.upload); })
      .catch((error) => {
        if (error.name !== "AbortError") setHistoryError(error.message);
      });
    return () => abort.abort();
  }, [reuseId]);

  useEffect(() => {
    if (!reuseId) return;
    const abort = new AbortController();
    api(`jobs/${reuseId}/`, { signal: abort.signal }).then(setOriginal).catch((failure) => {
      if (!abort.signal.aborted) setSourceError(failure.message);
    });
    return () => abort.abort();
  }, [reuseId, sourceAttempt]);

  function selectSource(type) {
    setSourceType(type);
    setErrors({});
    setMessage("");
    setDragging(false);
  }

  function fileIssue(file) {
    if (!file) return "Choose a video file.";
    if (upload && file.size > upload.max_bytes) return `Choose a video up to ${fileSize(upload.max_bytes)}.`;
    if (upload?.extensions?.length && !upload.extensions.some((extension) => file.name.toLowerCase().endsWith(extension))) {
      return "Choose a supported video file such as MP4, MOV, MKV, or WebM.";
    }
    return "";
  }

  function chooseVideo(file) {
    const issue = file ? fileIssue(file) : "";
    setDragging(false);
    setMessage("");
    setErrors(issue ? { video: [{ message: issue }] } : {});
    setVideo(issue ? null : file);
    if (videoInput.current && (!file || issue)) videoInput.current.value = "";
  }

  function changeOptions(next) {
    setOverrides((current) => ({ ...current, ...next }));
    setErrors({});
    setMessage("");
  }

  async function generate(event) {
    event.preventDefault();
    if (sending || preferencesLoading || (reuseId && !original?.can_reuse_source)) return;
    setErrors({});
    setMessage("");
    const issue = !reuseId && sourceType === "upload" && fileIssue(video);
    if (issue) {
      setErrors({ video: [{ message: issue }] });
      return;
    }
    setSending(true);
    setUploadPercent(null);
    try {
      const options = highlights ? { studio_mode, highlight_minutes, highlight_method, quality, language, caption_style }
        : { ...renderOptions, highlight_minutes: preferences.highlight_minutes };
      const job = reuseId ? await submit(`jobs/${reuseId}/alternate/`, options) : await (
        sourceType === "upload" ? submitUpload(video, options, setUploadPercent) : submit("jobs/", { source_url: source.trim(), ...options })
      );
      router.push(`/jobs/${job.id}`);
    } catch (error) {
      setErrors(error.fields || {});
      setMessage(Object.keys(error.fields || {}).length ? "" : error.message);
      setSending(false);
    }
  }

  if (reuseId && !original) return <section className="panel configure">
    <h1>Create another output</h1>
    {sourceError ? <><p className="field-error" role="alert">{sourceError}</p>
      <button className="secondary-button settings-retry" type="button" onClick={() => {
        setSourceError(""); setSourceAttempt((attempt) => attempt + 1);
      }}>Try again</button></> : <p className="muted" role="status">Loading the source render…</p>}
  </section>;

  const youtube = original ? original.source_type === "youtube" : sourceType === "youtube";

  return (
    <>
      <header className="page-heading">
        <div><h1>{original ? (highlights ? "Create a highlight reel" : "Create short clips") : "New render"}</h1>
          <p>{original ? "Use the original video to create a separate render and ZIP." :
            "Turn a YouTube video or a local upload into short clips or a highlight reel."}</p></div>
        {!original && <a className="secondary-button" href="#history"><ArrowDown size={16} aria-hidden="true" /> Recent renders</a>}
      </header>

      <div className="studio-grid">
        <section className="panel configure" aria-label="Render setup">
          <div className="panel-heading">
            <span className="heading-icon"><Clapperboard size={21} aria-hidden="true" /></span>
            <h2>Video source</h2>
          </div>
          <form id="new-render-form" onSubmit={generate}>
            {original ? <div className="studio-export-format reused-video">
              <FileVideo size={20} aria-hidden="true" />
              <div><strong>{original.source_name || `YouTube video ${original.video_id}`}</strong>
                <p>{original.studio_mode === "highlights" ? "From a Highlights render" : "From a Short Clips render"}</p>
                {!original.can_reuse_source && <p className="field-error" role="alert">The original video for this render is unavailable.</p>}
              </div>
            </div> : <fieldset className="control-group" disabled={sending}>
              <legend className="sr-only">Video source</legend>
              <div className="source-switch">
                <button type="button" className="secondary-button" aria-pressed={sourceType === "youtube"}
                  onClick={() => selectSource("youtube")}><Link2 size={17} aria-hidden="true" /> YouTube link</button>
                <button type="button" className="secondary-button" aria-pressed={sourceType === "upload"}
                  onClick={() => selectSource("upload")}><Upload size={17} aria-hidden="true" /> Upload video</button>
              </div>
              {sourceType === "youtube" ? (
                <>
                  <label htmlFor="source-url">YouTube URL</label>
                  <div className="input-wrap source">
                    <Link2 size={19} aria-hidden="true" />
                    <input id="source-url" ref={sourceInput} type="url" value={source} onChange={(event) => {
                      setSource(event.target.value); setErrors({}); setMessage("");
                    }}
                      placeholder="https://www.youtube.com/watch?v=…" autoComplete="url" required
                      aria-invalid={Boolean(errors.source_url)} aria-describedby={errors.source_url ? "url-error" : undefined} />
                  </div>
                  {errors.source_url && <p className="field-error" id="url-error" role="alert">{errors.source_url[0].message}</p>}
                </>
              ) : (
                <>
                  <label className="upload-picker" htmlFor="source-video" data-dragging={dragging}
                    onDragOver={(event) => {
                      if (!sending && event.dataTransfer.types.includes("Files")) {
                        event.preventDefault(); setDragging(true);
                      }
                    }}
                    onDragLeave={(event) => {
                      if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false);
                    }}
                    onDrop={(event) => {
                      if (!event.dataTransfer.types.includes("Files")) return;
                      event.preventDefault();
                      if (!sending && event.dataTransfer.files.length) chooseVideo(event.dataTransfer.files[0]);
                    }}>
                    {video ? <FileVideo size={22} aria-hidden="true" /> : <Upload size={21} aria-hidden="true" />}
                    <span><strong>{video?.name || "Choose or drop a video"}</strong>
                      <small id="upload-hint">{video ? fileSize(video.size) :
                        `MP4, MOV, MKV, WebM and more${upload ? ` · up to ${fileSize(upload.max_bytes)}` : ""}`}</small></span>
                    <input id="source-video" ref={videoInput} aria-label="Choose a video file" type="file"
                      accept={upload?.extensions.join(",") || "video/*"} aria-invalid={Boolean(errors.video)}
                      aria-describedby={errors.video ? "upload-hint upload-error" : "upload-hint"}
                      onChange={(event) => chooseVideo(event.target.files[0] || null)} />
                  </label>
                  {video && <button type="button" className="studio-remove-file" onClick={() => {
                    chooseVideo(null); videoInput.current?.focus();
                  }}><X size={14} aria-hidden="true" /> Remove file</button>}
                  {errors.video && <p className="field-error" id="upload-error" role="alert">{errors.video[0].message}</p>}
                </>
              )}
            </fieldset>}
            <h2 className="studio-output-title">Output</h2>
            {!original && <div className="studio-modes" role="group" aria-label="Studio mode">
              {modes.map(([mode, Icon, label, description]) => <button key={mode} type="button" className="studio-mode"
                aria-pressed={studio_mode === mode} disabled={sending} onClick={() => changeOptions({ studio_mode: mode })}>
                <span className="heading-icon"><Icon size={19} aria-hidden="true" /></span>
                <span><strong>{label}</strong><small>{description}</small></span>
              </button>)}
            </div>}
            <fieldset className="control-group" disabled={preferencesLoading || sending}>
              <legend className="sr-only">Render options</legend>
              {highlights ? <HighlightOptions value={renderOptions} onChange={changeOptions} showShared /> :
                <RenderOptions value={renderOptions} onChange={changeOptions} compact showQuality={youtube} />}
            </fieldset>
          </form>
        </section>

        <aside className="panel export-panel" aria-labelledby="export-title">
          <div className="panel-heading">
            <span className="heading-icon"><Archive size={20} aria-hidden="true" /></span>
            <h2 id="export-title">ZIP export</h2>
          </div>
          <div className="studio-export-format">
            <Film size={20} aria-hidden="true" />
            <div><strong>{highlights ? "Landscape highlight reel" : `${editingStyles[renderOptions.editing_style]?.[0] || "Balanced"} clip pack`}</strong>
              <p>{[highlights ? "16:9" : renderOptions.aspect_ratio, outputLabel(renderOptions),
                (youtube || highlights) && `${quality}p`].filter(Boolean).join(" · ")}</p></div>
          </div>
          <ul className="studio-export-files" aria-label="Included in the ZIP">
            <li><CheckCircle2 size={16} aria-hidden="true" /> Titled MP4 {highlights ? "reel" : "clips"}</li>
            <li><CheckCircle2 size={16} aria-hidden="true" /> JPG {highlights ? "cover" : "covers"} and TXT metadata</li>
            {caption_style !== "off" && <li><CheckCircle2 size={16} aria-hidden="true" /> SRT when captions are available</li>}
          </ul>
          <div className="studio-submit">
            {message && <p role="alert" className="field-error">{message}</p>}
            {Object.entries(errors).filter(([field]) => !["source_url", "video"].includes(field)).map(([field, issues]) => (
              <p role="alert" className="field-error" key={field}>{issues[0].message}</p>
            ))}
            {sending && !original && !youtube && <progress className="upload-progress" max={100}
              value={uploadPercent ?? undefined} aria-label="Video upload progress" />}
            <button form="new-render-form" type="submit" className="primary-button generate"
              disabled={sending || preferencesLoading || (original && !original.can_reuse_source)}>
              <Sparkles size={19} aria-hidden="true" /> {sending
                ? (!original && !youtube && uploadPercent !== 100 ? `Uploading${uploadPercent === null ? "…" : ` ${uploadPercent}%`}` : "Queuing render…")
                : highlights ? "Generate highlights" : "Generate clips"}
            </button>
          </div>
        </aside>
      </div>

      {!original && <section id="history" className="history" aria-labelledby="history-title">
        <div className="section-heading"><h2 id="history-title">Recent renders</h2>
          <Link href="/library" className="secondary-button">View library</Link></div>
        <div className="panel history-panel">
          {historyError ? <p className="empty-state" role="alert">{historyError}</p> :
            !history ? <p className="empty-state">Loading renders…</p> :
            history.length === 0 ? (
              <div className="empty-state">
                <span className="export-icon"><Film size={20} aria-hidden="true" /></span>
                <div><strong>No renders yet</strong><p>Your clips and highlight reels will appear here.</p></div>
              </div>
            ) : history.map((job) => {
              const complete = job.status === "complete";
              const reel = job.studio_mode === "highlights";
              const selection = reel ? `Highlights · up to ${job.highlight_minutes} min` :
                complete ? `${job.clips.length} ${job.clips.length === 1 ? "clip" : "clips"}` :
                job.selection_mode === "score" ? `${job.editing_style === "gameplay" ? "Activity" : "Viral"} score ${job.min_viral_score}+` : `Up to ${job.clip_count} clips`;
              return (
                <Link href={`/jobs/${job.id}`} className="history-row" key={job.id}>
                  <span className="export-icon">{reel ? <LibraryBig size={19} aria-hidden="true" /> : <Film size={19} aria-hidden="true" />}</span>
                  <span className="history-name">
                    <strong>{job.clips[0]?.title || job.source_name || `YouTube video ${job.video_id}`}</strong>
                    <small>{selection} · {job.aspect_ratio}{!reel && job.editing_style && ` · ${editingStyles[job.editing_style]?.[0]}`}</small>
                  </span>
                  <span className={`status ${complete && !job.clips.length ? "queued" : job.status}`}>
                    {complete && !job.clips.length ? "No matches" : statusLabels[job.status]}
                  </span>
                </Link>
              );
            })}
        </div>
      </section>}
    </>
  );
}
