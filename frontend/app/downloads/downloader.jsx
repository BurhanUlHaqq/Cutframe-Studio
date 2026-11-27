"use client";

import { useEffect, useState } from "react";
import { ArrowDownToLine, ChevronLeft, ChevronRight, Download, Film, Globe2, Instagram, Link2, MessageCircle, Music2, RotateCcw, Square, Trash2, Twitch, Youtube } from "lucide-react";
import { api, submit } from "../api";
import { SelectField } from "../render-options";

const sourceDetails = {
  YouTube: ["Videos, Shorts, and finished replays", Youtube],
  Vimeo: ["Video and player links", Film],
  Dailymotion: ["Video links", Film],
  TikTok: ["Video links and short share links", Music2],
  Instagram: ["Public reels and video posts", Instagram],
  Twitch: ["Clips and recorded videos", Twitch],
  Reddit: ["Posts with Reddit-hosted video", MessageCircle],
};
const statusLabels = { queued: "Queued", running: "Downloading", cancelling: "Stopping", cancelled: "Stopped", complete: "Ready", failed: "Failed" };
const defaultQualities = { best: "Best available", 1080: "Prefer 1080p", 720: "Prefer 720p", 480: "Prefer 480p" };

function formatBytes(value) {
  const exponent = Math.min(3, Math.floor(Math.log(Math.max(1, value)) / Math.log(1024)));
  const labels = ["B", "KiB", "MiB", "GiB"];
  const displayed = new Intl.NumberFormat(undefined, { maximumFractionDigits: exponent ? 1 : 0 }).format(value / 1024 ** exponent);
  return `${displayed} ${labels[exponent]}`;
}

function formatDuration(seconds) {
  const rounded = Math.round(seconds);
  const parts = [Math.floor(rounded / 3600), Math.floor((rounded % 3600) / 60), rounded % 60];
  return parts.slice(parts[0] ? 0 : 1).map((part, index) => index ? String(part).padStart(2, "0") : String(part)).join(":");
}

function DownloadRow({ job, pending, onAction }) {
  const Icon = sourceDetails[job.platform]?.[1] || Film;
  const ready = job.status === "complete";
  const active = job.status === "running" || job.status === "cancelling";
  const canRetry = job.status === "failed" || job.status === "cancelled";
  const waiting = active || job.status === "queued";
  const metadata = [job.platform, ready ? job.extension.toUpperCase() : null,
    job.width && job.height ? `${job.width} × ${job.height}` : null, job.duration ? formatDuration(job.duration) : null,
    ready ? formatBytes(job.bytes_received) : null].filter(Boolean).join(" · ");
  return (
    <article className="download-row">
      <div className="download-row-body">
        <span className="download-source-icon"><Icon size={20} aria-hidden="true" /></span>
        <div className="download-row-text">
          <h3>{job.title || `${job.platform} video`}</h3>
          <p>{metadata}</p>
          {!ready && <a className="download-original" href={job.url} target="_blank" rel="noreferrer"
            title={job.url}>{job.url.replace(/^https:\/\//, "")}</a>}
          {!ready && <p className={job.status === "failed" ? "download-status-error" : undefined}>{job.activity}</p>}
          {active && <div className="download-progress">
            <progress max={job.bytes_expected || 100}
              value={job.bytes_expected ? Math.min(job.bytes_received, job.bytes_expected * 0.99) : undefined}
              aria-label={`Transfer progress for ${job.title || job.platform}`} />
            {job.bytes_received > 0 && <small>{formatBytes(job.bytes_received)} received</small>}
          </div>}
        </div>
      </div>
      <div className="download-row-actions">
        <span className="download-status" data-status={job.status}>{statusLabels[job.status]}</span>
        {ready && <a className="primary-button" href={job.file_url} download>
          <Download size={16} aria-hidden="true" /> Save video
        </a>}
        {waiting && job.status !== "cancelling" && <button className="secondary-button" type="button" disabled={pending}
          onClick={() => onAction("stop", job)}><Square size={14} aria-hidden="true" /> Stop</button>}
        {canRetry && <button className="secondary-button" type="button" disabled={pending}
          onClick={() => onAction("retry", job)}><RotateCcw size={15} aria-hidden="true" /> Retry</button>}
        {!waiting && <button className="secondary-button download-remove" type="button" disabled={pending}
          aria-label={`Delete ${job.title || job.platform + " video"}`} title="Delete from Cutframe"
          onClick={() => onAction("delete", job)}><Trash2 size={16} aria-hidden="true" /></button>}
      </div>
    </article>
  );
}

export default function Downloader() {
  const [text, setText] = useState("");
  const [quality, setQuality] = useState("best");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState(null);
  const [loadingError, setLoadingError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [queueing, setQueueing] = useState(false);
  const [pending, setPending] = useState("");
  const links = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const batchLimit = data?.batch_limit ?? 8;
  const qualities = data?.qualities ?? defaultQualities;

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    async function readQueue() {
      let delay = 6500;
      try {
        const result = await api(`downloads/?page=${page}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setData(result);
        setLoadingError("");
        if (result.page !== page) setPage(result.page);
        if (result.active) delay = 1600;
      } catch (failure) {
        if (!controller.signal.aborted) setLoadingError(failure.message);
      }
      if (!controller.signal.aborted) timer = setTimeout(readQueue, delay);
    }
    readQueue();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [page, revision]);

  async function queue(event) {
    event.preventDefault();
    if (!links.length || links.length > batchLimit || queueing) return;
    setQueueing(true);
    setError("");
    setNotice("");
    try {
      const result = await submit("downloads/", { urls: links, quality });
      const count = result.downloads.length;
      setNotice(`${count} video${count === 1 ? "" : "s"} added to downloads.`);
      setText("");
      setPage(1);
      setRevision((value) => value + 1);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setQueueing(false);
    }
  }

  async function action(command, job) {
    if (pending || (command === "delete" && !window.confirm("Delete this download from Cutframe?"))) return;
    setPending(job.id);
    setError("");
    setNotice("");
    try {
      const result = await submit(`downloads/${job.id}/${command}/`, {});
      setData((current) => current && { ...current, downloads: command === "delete"
        ? current.downloads.filter((row) => row.id !== job.id)
        : current.downloads.map((row) => row.id === result.id ? result : row) });
      setRevision((value) => value + 1);
      if (command === "delete") setNotice("Download removed.");
    } catch (failure) {
      setError(failure.message);
    } finally {
      setPending("");
    }
  }

  return (
    <section className="downloader-page" aria-labelledby="downloader-title">
      <header className="page-heading">
        <div><h1 id="downloader-title">Video downloader</h1><p>Save videos from supported sources to your device.</p></div>
      </header>
      <div className="download-compose">
        <form className="panel settings-panel download-form" onSubmit={queue}>
          <div className="panel-heading">
            <span className="heading-icon"><Link2 size={20} aria-hidden="true" /></span>
            <div><h2>Video links</h2><p>Paste one video link per line, up to {batchLimit} at a time.</p></div>
          </div>
          <label className="sr-only" htmlFor="download-links">Video URLs</label>
          <textarea id="download-links" rows={4} value={text} placeholder="https://www.youtube.com/watch?v=…"
            spellCheck={false} autoCapitalize="none" maxLength={batchLimit * 605} disabled={queueing}
            aria-describedby="download-links-hint" onChange={(event) => { setText(event.target.value); setError(""); }} />
          <div className="download-input-hint" id="download-links-hint">
            <small>Public videos you have permission to save · Up to {formatBytes(data?.file_limit ?? 4 * 1024 ** 3)} each</small>
            <small className={links.length > batchLimit ? "field-error" : undefined}>{links.length}/{batchLimit} links</small>
          </div>
          <div className="download-submit">
            <div><SelectField label="Resolution preference" value={quality} choices={Object.entries(qualities).sort(([a], [b]) =>
              a === "best" ? -1 : b === "best" ? 1 : Number(b) - Number(a))} onChange={setQuality} /></div>
            <button className="primary-button" type="submit" disabled={queueing || !links.length || links.length > batchLimit}>
              <ArrowDownToLine size={17} aria-hidden="true" /> {queueing ? "Adding…" : links.length > 1 ? "Download videos" : "Download video"}
            </button>
          </div>
        </form>
        <aside className="panel settings-panel download-sources" aria-labelledby="download-sources-title">
          <div className="panel-heading">
            <span className="heading-icon"><Globe2 size={20} aria-hidden="true" /></span><h2 id="download-sources-title">Supported sources</h2>
          </div>
          <ul>{(data?.platforms ?? Object.keys(sourceDetails)).map((name) => {
            const [detail, Icon] = sourceDetails[name] ?? ["Individual video links", Film];
            return <li key={name}><Icon size={17} aria-hidden="true" /><span><strong>{name}</strong><small>{detail}</small></span></li>;
          })}</ul>
        </aside>
      </div>
      <section className="download-history" aria-labelledby="download-history-title">
        <div className="section-heading">
          <h2 id="download-history-title">Downloads</h2>
          {data?.active > 0 && <span className="download-active">{data.active} active</span>}
        </div>
        <div className="download-feedback" aria-live="polite">
          {notice && <p className="settings-saved">{notice}</p>}
          {error && <p className="field-error" role="alert">{error}</p>}
          {loadingError && <div className="download-load-error">
            <p className="field-error">{loadingError}</p>
            <button className="secondary-button" type="button" onClick={() => setRevision((value) => value + 1)}>Refresh downloads</button>
          </div>}
        </div>
        {data ? (
          data.downloads.length ? <div className="panel download-list">
            {data.downloads.map((job) => <DownloadRow key={job.id} job={job} onAction={action} pending={Boolean(pending)} />)}
          </div> : <div className="panel download-empty"><Download size={21} aria-hidden="true" /><p>Downloaded videos will appear here.</p></div>
        ) : !loadingError && <p className="muted" role="status">Loading downloads…</p>}
        {data?.pages > 1 && <nav className="download-pagination" aria-label="Download history pages">
          <button className="secondary-button" type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            <ChevronLeft size={16} aria-hidden="true" /> Previous</button>
          <span>Page {data.page} of {data.pages}</span>
          <button className="secondary-button" type="button" disabled={page >= data.pages} onClick={() => setPage(page + 1)}>
            Next <ChevronRight size={16} aria-hidden="true" /></button>
        </nav>}
      </section>
    </section>
  );
}
