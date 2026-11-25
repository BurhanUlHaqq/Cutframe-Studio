"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Archive, Captions, Clapperboard, FileText, Film, Image as ImageIcon, Link2, Sparkles, Upload,
} from "lucide-react";
import { api, statusLabels, submit, submitUpload } from "./api";
import { usePreferences } from "./preferences";
import RenderOptions from "./render-options";

const exportItems = [
  [Film, "MP4 clips", "Named after each clip’s title"],
  [ImageIcon, "JPG thumbnails", "A sharp, caption-free frame from each clip"],
  [FileText, "TXT metadata", "A title and ready-to-post description"],
  [Captions, "SRT captions", "Included with captioned clips"],
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
  const { selection_mode, clip_count, min_viral_score, aspect_ratio, quality, language, framing, caption_style,
    min_clip_seconds, max_clip_seconds } =
    { ...preferences, ...overrides };
  const renderOptions = { selection_mode, clip_count, min_viral_score, aspect_ratio, quality, language, framing,
    caption_style, min_clip_seconds, max_clip_seconds };
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [history, setHistory] = useState(null);
  const [historyError, setHistoryError] = useState("");

  useEffect(() => {
    const abort = new AbortController();
    api("jobs/", { signal: abort.signal })
      .then((data) => { setHistory(data.jobs); setUpload(data.upload); })
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
      const job = sourceType === "upload" ? await submitUpload(video, renderOptions, setUploadPercent) :
        await submit("jobs/", { source_url: source.trim(), ...renderOptions });
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
          <h1 id="hero-title">Long video.<br />Short clips.</h1>
          <p>Find the best moments in a YouTube video or a file on your device.</p>
        </div>
        <div className="hero-art" aria-hidden="true">
          <Image src="/studio-hero.jpg" alt="" fill priority sizes="(max-width: 800px) 60vw, 580px" />
          <div className="frame-corners"><i /><i /><i /><i /></div>
        </div>
      </section>

      <div className="studio-grid">
        <section className="panel configure" aria-labelledby="configure-title">
          <div className="panel-heading">
            <span className="heading-icon"><Clapperboard size={21} aria-hidden="true" /></span>
            <h2 id="configure-title">Create a clip pack</h2>
          </div>
          <form onSubmit={generate}>
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
              <RenderOptions value={renderOptions} onChange={(next) => setOverrides((current) => ({ ...current, ...next }))}
                compact showQuality={sourceType === "youtube"} />
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
                : "Generate clips"}
            </button>
          </form>
        </section>

        <aside className="panel export-panel" aria-labelledby="export-title">
          <div className="panel-heading">
            <span className="heading-icon"><Archive size={20} aria-hidden="true" /></span>
            <h2 id="export-title">What’s in your ZIP</h2>
          </div>
          <ul className="export-list">
            {exportItems.map(([Icon, title, subtitle]) => (
              <li key={title}>
                <span className="export-icon"><Icon size={19} aria-hidden="true" /></span>
                <span><strong>{title}</strong><small>{subtitle}</small></span>
              </li>
            ))}
          </ul>
        </aside>
      </div>

      <section id="history" className="history" aria-labelledby="history-title">
        <div className="section-heading"><h2 id="history-title">Render history</h2>{history?.length > 0 && <span>{history.length} recent</span>}</div>
        <div className="panel history-panel">
          {historyError ? <p className="empty-state" role="alert">{historyError}</p> :
            !history ? <p className="empty-state">Loading renders…</p> :
            history.length === 0 ? (
              <div className="empty-state">
                <span className="export-icon"><Film size={20} aria-hidden="true" /></span>
                <div><strong>No renders yet</strong><p>Clip packs you create will appear here.</p></div>
              </div>
            ) : history.map((job) => {
              const complete = job.status === "complete";
              const selection = complete ? `${job.clips.length} ${job.clips.length === 1 ? "clip" : "clips"}` :
                job.selection_mode === "score" ? `Viral score ${job.min_viral_score}+` : `Up to ${job.clip_count} clips`;
              return (
                <Link href={`/jobs/${job.id}`} className="history-row" key={job.id}>
                  <span className="export-icon"><Film size={19} aria-hidden="true" /></span>
                  <span className="history-name">
                    <strong>{job.clips[0]?.title || job.source_name || `YouTube video ${job.video_id}`}</strong>
                    <small>{selection} · {job.aspect_ratio}</small>
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
