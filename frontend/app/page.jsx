"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Archive, Captions, Clapperboard, FileText, Film, Image as ImageIcon, Link2, LibraryBig, Sparkles, Upload,
} from "lucide-react";
import { api, statusLabels, submit, submitUpload } from "./api";
import { usePreferences } from "./preferences";
import RenderOptions, { editingStyles, HighlightOptions } from "./render-options";

const exportItems = [
  [Film, "MP4 clips", "Named after each clip’s title"],
  [ImageIcon, "JPG thumbnails", "An editable cover for each clip"],
  [FileText, "TXT metadata", "Title, description, and topic hashtags"],
  [Captions, "SRT captions", "Included with captioned clips"],
];
const reelItems = [
  [Film, "One landscape MP4", "Selected moments in source order"],
  [ImageIcon, "JPG thumbnail", "An editable cover from the selected moments"],
  [FileText, "TXT metadata", "Description, topic hashtags, and eligible chapters"],
  [Captions, "SRT captions", "Timed to the finished reel when captions are enabled"],
];
const modes = [
  ["shorts", Film, "Short Clips", "A pack of standalone clips"],
  ["highlights", LibraryBig, "Highlights", "One landscape reel up to 30 minutes"],
];

function fileSize(bytes) {
  const gb = 1024 ** 3;
  return bytes >= gb ? `${(bytes / gb).toFixed(1).replace(/\.0$/, "")} GB` :
    `${Math.max(0.1, bytes / 1024 ** 2).toFixed(1).replace(/\.0$/, "")} MB`;
}

export default function Studio() {
  const router = useRouter();
  const { preferences, loading: preferencesLoading } = usePreferences();
  const [source, setSource] = useState("");
  const [sourceType, setSourceType] = useState("youtube");
  const [video, setVideo] = useState(null);
  const [upload, setUpload] = useState(null);
  const [uploadPercent, setUploadPercent] = useState(null);
  const [overrides, setOverrides] = useState({});
  const { theme, show_scores, ...renderOptions } = { ...preferences, ...overrides };
  const { studio_mode, highlight_minutes, highlight_method, quality, language, caption_style } = renderOptions;
  const highlights = studio_mode === "highlights";
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [history, setHistory] = useState(null);
  const [historyError, setHistoryError] = useState("");

  useEffect(() => {
    const abort = new AbortController();
    api("jobs/", { signal: abort.signal })
      .then((data) => { setHistory(data.jobs.slice(0, 5)); setUpload(data.upload); })
      .catch((error) => {
        if (error.name !== "AbortError") setHistoryError(error.message);
      });
    return () => abort.abort();
  }, []);

  function selectSource(type) {
    setSourceType(type);
    setErrors({});
    setMessage("");
    if (type === "youtube") setVideo(null);
  }

  function changeOptions(next) {
    setOverrides((current) => ({ ...current, ...next }));
    setErrors({});
    setMessage("");
  }

  async function generate(event) {
    event.preventDefault();
    if (sending || preferencesLoading) return;
    setErrors({});
    setMessage("");
    if (sourceType === "upload" && (!video || (upload && video.size > upload.max_bytes))) {
      setErrors({ video: [{ message: video ? `Choose a video up to ${fileSize(upload.max_bytes)}.` : "Choose a video file." }] });
      return;
    }
    setSending(true);
    setUploadPercent(null);
    try {
      const options = highlights ? { studio_mode, highlight_minutes, highlight_method, quality, language, caption_style }
        : { ...renderOptions, highlight_minutes: preferences.highlight_minutes };
      const job = sourceType === "upload" ? await submitUpload(video, options, setUploadPercent) :
        await submit("jobs/", { source_url: source.trim(), ...options });
      router.push(`/jobs/${job.id}`);
    } catch (error) {
      setErrors(error.fields || {});
      setMessage(Object.keys(error.fields || {}).length ? "" : error.message);
      if (error.fields?.source_url) document.getElementById("source-url")?.focus();
      if (error.fields?.video) document.getElementById("source-video")?.focus();
      setSending(false);
    }
  }

  return (
    <>
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero-copy">
          <h1 id="hero-title">Keep the<br />best moments.</h1>
          <p>Turn a YouTube video or an upload into short clips or a highlight reel.</p>
        </div>
        <div className="hero-art" aria-hidden="true">
          <Image src="/studio-hero.jpg" alt="" fill priority sizes="(max-width: 800px) 60vw, 580px" />
          <div className="frame-corners"><i /><i /><i /><i /></div>
        </div>
      </section>

      <div className="studio-modes" role="group" aria-label="Studio mode">
        {modes.map(([mode, Icon, label, description]) => <button key={mode} type="button" className="panel studio-mode"
          aria-pressed={studio_mode === mode} disabled={sending} onClick={() => changeOptions({ studio_mode: mode })}>
          <span className="heading-icon"><Icon size={21} aria-hidden="true" /></span>
          <span><strong>{label}</strong><small>{description}</small></span>
        </button>)}
      </div>

      <div className="studio-grid">
        <section className="panel configure" aria-labelledby="configure-title">
          <div className="panel-heading">
            <span className="heading-icon"><Clapperboard size={21} aria-hidden="true" /></span>
            <h2 id="configure-title">{highlights ? "Create a highlight reel" : "Create a clip pack"}</h2>
          </div>
          <form onSubmit={generate} className={highlights ? "highlight-form" : undefined}>
            <fieldset className="control-group" disabled={sending}>
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
                    <input id="source-url" type="url" value={source} onChange={(event) => setSource(event.target.value)}
                      placeholder="https://www.youtube.com/watch?v=…" autoComplete="url" required
                      aria-invalid={Boolean(errors.source_url)} aria-describedby={errors.source_url ? "url-error" : undefined} />
                  </div>
                  {errors.source_url && <p className="field-error" id="url-error">{errors.source_url[0].message}</p>}
                </>
              ) : (
                <>
                  <label className="upload-picker" htmlFor="source-video">
                    <Upload size={21} aria-hidden="true" />
                    <span><strong>{video?.name || "Choose a video file"}</strong>
                      <small id="upload-hint">{video ? fileSize(video.size) :
                        `MP4, MOV, MKV, WebM and more${upload ? ` · up to ${fileSize(upload.max_bytes)}` : ""}`}</small></span>
                    <input id="source-video" aria-label="Choose a video file" type="file" required
                      accept={upload?.extensions.join(",") || "video/*"} aria-invalid={Boolean(errors.video)}
                      aria-describedby={errors.video ? "upload-hint upload-error" : "upload-hint"}
                      onChange={(event) => { setVideo(event.target.files[0] || null); setErrors({}); setMessage(""); }} />
                  </label>
                  {errors.video && <p className="field-error" id="upload-error">{errors.video[0].message}</p>}
                </>
              )}
            </fieldset>
            <fieldset className="control-group" disabled={preferencesLoading || sending}>
              <legend className="sr-only">Render options</legend>
              {highlights ? <HighlightOptions value={renderOptions} onChange={changeOptions} showShared /> :
                <RenderOptions value={renderOptions} onChange={changeOptions} compact showQuality={sourceType === "youtube"} />}
            </fieldset>
            {message && <p role="alert" className="field-error">{message}</p>}
            {Object.entries(errors).filter(([field]) => !["source_url", "video"].includes(field)).map(([field, issues]) => (
              <p role="alert" className="field-error" key={field}>{issues[0].message}</p>
            ))}
            {sending && sourceType === "upload" && <progress className="upload-progress" max={100}
              value={uploadPercent ?? undefined} aria-label="Video upload progress" />}
            <button type="submit" className="primary-button generate" disabled={sending || preferencesLoading}>
              <Sparkles size={19} aria-hidden="true" /> {sending
                ? (sourceType === "upload" && uploadPercent !== 100 ? `Uploading${uploadPercent === null ? "…" : ` ${uploadPercent}%`}` : "Queuing render…")
                : highlights ? "Generate highlights" : "Generate clips"}
            </button>
          </form>
        </section>

        <aside className="panel export-panel" aria-labelledby="export-title">
          <div className="panel-heading">
            <span className="heading-icon"><Archive size={20} aria-hidden="true" /></span>
            <h2 id="export-title">What’s in your ZIP</h2>
          </div>
          <ul className="export-list">
            {(highlights ? reelItems : exportItems).map(([Icon, title, subtitle]) => (
              <li key={title}>
                <span className="export-icon"><Icon size={19} aria-hidden="true" /></span>
                <span><strong>{title}</strong><small>{subtitle}</small></span>
              </li>
            ))}
          </ul>
        </aside>
      </div>

      <section id="history" className="history" aria-labelledby="history-title">
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
      </section>
    </>
  );
}
