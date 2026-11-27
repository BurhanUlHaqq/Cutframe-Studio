"use client";

import { useEffect, useState } from "react";
import { ArrowDownToLine, ChevronLeft, ChevronRight, Download, FileArchive, Film, Globe2, Instagram, Link2, MessageCircle, Music2, RotateCcw, Square, Trash2, Twitch, Youtube } from "lucide-react";
import { api, submit } from "../api";
import { SelectField } from "../render-options";
import SourceRangeOptions, { formatSourceTimestamp } from "../source-range-options";

const sourceDetails = {
  YouTube: ["Videos, Shorts, and playlists", Youtube],
  Vimeo: ["Videos and showcases", Film],
  Dailymotion: ["Videos and playlists", Film],
  TikTok: ["Video links and short share links", Music2],
  Instagram: ["Public reels and video posts", Instagram],
  Twitch: ["Clips and recorded videos", Twitch],
  Reddit: ["Posts with Reddit-hosted video", MessageCircle],
};
const statusLabels = { queued: "Queued", running: "Downloading", cancelling: "Stopping", cancelled: "Stopped", complete: "Ready", partial: "Partially ready", failed: "Failed" };
const defaultQualities = { best: "Best available", 4320: "8K (4320p)", 2160: "4K (2160p)", 1440: "1440p",
  1080: "1080p", 720: "720p", 480: "480p", 360: "360p", 240: "240p", 144: "144p" };

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

function PlaylistItems({ job }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");
  const { items_url, ready, failed, total } = job.playlist;

  useEffect(() => {
    const controller = new AbortController();
    api(items_url, { signal: controller.signal }).then((data) => {
      setItems(data.items);
      setError("");
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [items_url, job.status, job.activity, ready, failed, total]);

  if (error) return <p className="field-error playlist-items-message" role="alert">{error}</p>;
  if (!items) return <p className="muted playlist-items-message" role="status">Loading playlist items…</p>;
  if (!items.length) return <p className="muted playlist-items-message">Playlist items will appear here after loading.</p>;
  return <ol className="playlist-items">
    {items.map((item) => <li key={item.id}>
      <span className="playlist-item-position">{item.position}</span>
      <div className="playlist-item-info">
        <strong>{item.title}</strong>
        <small>{item.file_url ? [
          item.extension.toUpperCase(), item.width && item.height ? `${item.width} × ${item.height}` : "",
          formatDuration(item.duration), formatBytes(item.bytes_received),
        ].filter(Boolean).join(" · ") : item.activity}</small>
      </div>
      {item.file_url ? <a className="secondary-button" href={item.file_url} download aria-label={`Save ${item.title}`}>
        <Download size={15} aria-hidden="true" /> Save
      </a> : <span className="download-status" data-status={item.status}>{statusLabels[item.status]}</span>}
    </li>)}
  </ol>;
}

function DownloadRow({ job, pending, onAction }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = sourceDetails[job.platform]?.[1] || Film;
  const ready = job.status === "complete";
  const active = job.status === "running" || job.status === "cancelling";
  const canRetry = ["failed", "cancelled", "partial"].includes(job.status);
  const waiting = active || job.status === "queued";
  const metadata = job.is_playlist ? [
    job.platform, job.output === "mp3" ? "MP3 playlist" : "Video playlist",
    job.playlist.total > 0 ? `${job.playlist.ready}/${job.playlist.total} ready` : "",
  ].filter(Boolean).join(" · ") : [job.platform, ready ? job.extension.toUpperCase() : job.output === "mp3" ? "MP3" : null,
    job.width && job.height ? `${job.width} × ${job.height}` : null, job.duration ? formatDuration(job.duration) : null,
    ready ? formatBytes(job.bytes_received) : null].filter(Boolean).join(" · ");
  return (
    <article className="download-entry">
      <div className="download-row">
        <div className="download-row-body">
          <span className="download-source-icon"><Icon size={20} aria-hidden="true" /></span>
          <div className="download-row-text">
            <h3>{job.title || `${job.platform} ${job.is_playlist ? "playlist" : "video"}`}</h3>
            <p>{metadata}</p>
            {job.source_start_ms != null && job.source_end_ms != null && <p>
              Source range {formatSourceTimestamp(job.source_start_ms)}–{formatSourceTimestamp(job.source_end_ms)}
            </p>}
            {!ready && <a className="download-original" href={job.url} target="_blank" rel="noreferrer"
              title={job.url}>{job.url.replace(/^https:\/\//, "")}</a>}
            {(!ready || job.is_playlist) && <p className={job.status === "failed" ? "download-status-error" : undefined}>{job.activity}</p>}
            {job.playlist?.limited && <p>More items are available. Queue another range starting at {job.playlist.start + job.playlist.total}.</p>}
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
          {job.is_playlist && job.playlist.total > 0 && <button className="secondary-button" type="button"
            aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "Hide items" : "View items"}</button>}
          {job.playlist?.zip_url && <a className="primary-button" href={job.playlist.zip_url} download>
            <FileArchive size={16} aria-hidden="true" /> Save ZIP
          </a>}
          {job.file_url && <a className="primary-button" href={job.file_url} download>
            <Download size={16} aria-hidden="true" /> {job.output === "mp3" ? "Save MP3" : "Save video"}
          </a>}
          {waiting && job.status !== "cancelling" && <button className="secondary-button" type="button" disabled={pending}
            onClick={() => onAction("stop", job)}><Square size={14} aria-hidden="true" /> Stop</button>}
          {canRetry && <button className="secondary-button" type="button" disabled={pending}
            onClick={() => onAction("retry", job)}><RotateCcw size={15} aria-hidden="true" /> {job.is_playlist ? "Retry unfinished" : "Retry"}</button>}
          {!waiting && <button className="secondary-button download-remove" type="button" disabled={pending}
            aria-label={`Delete ${job.title || job.platform + " video"}`} title="Delete from Cutframe"
            onClick={() => onAction("delete", job)}><Trash2 size={16} aria-hidden="true" /></button>}
        </div>
      </div>
      {expanded && <PlaylistItems job={job} />}
    </article>
  );
}

export default function Downloader() {
  const [text, setText] = useState("");
  const [quality, setQuality] = useState("best");
  const [output, setOutput] = useState("video");
  const [bitrate, setBitrate] = useState("192");
  const [playlistStart, setPlaylistStart] = useState("1");
  const [playlistEnd, setPlaylistEnd] = useState("");
  const [linkedPlaylist, setLinkedPlaylist] = useState(false);
  const [sourceRange, setSourceRange] = useState({});
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
  const playlistLimit = data?.playlist_limit ?? 100;
  const bitrates = (data?.audio_bitrates ?? [128, 192, 256, 320]).map((rate) => [String(rate), `${rate} kbps`]);

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
      const result = await submit("downloads/", {
        urls: links, quality, output, audio_bitrate: Number(bitrate), linked_playlist: linkedPlaylist, ...sourceRange,
        playlist_start: Number(playlistStart || 1), playlist_end: playlistEnd ? Number(playlistEnd) : null,
      });
      const count = result.downloads.length;
      setNotice(`${count} download${count === 1 ? "" : "s"} queued.`);
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
        <div><h1 id="downloader-title">Video downloader</h1><p>Save videos, MP3 audio, and playlists from supported sources.</p></div>
      </header>
      <div className="download-compose">
        <form className="panel settings-panel download-form" onSubmit={queue}>
          <div className="panel-heading">
            <span className="heading-icon"><Link2 size={20} aria-hidden="true" /></span>
            <div><h2>Media links</h2><p>Paste one video or playlist link per line, up to {batchLimit} at a time.</p></div>
          </div>
          <label className="sr-only" htmlFor="download-links">Video or playlist URLs</label>
          <textarea id="download-links" rows={4} value={text} placeholder="https://www.youtube.com/watch?v=…"
            spellCheck={false} autoCapitalize="none" maxLength={batchLimit * 605} disabled={queueing}
            aria-describedby="download-links-hint" onChange={(event) => { setText(event.target.value); setError(""); }} />
          <div className="download-input-hint" id="download-links-hint">
            <small>Public videos you have permission to save · Up to {formatBytes(data?.file_limit ?? 16 * 1024 ** 3)} each</small>
            <small className={links.length > batchLimit ? "field-error" : undefined}>{links.length}/{batchLimit} links</small>
          </div>
          <div className="download-format">
            <SelectField label="Output format" value={output} choices={[["video", "Video"], ["mp3", "MP3 audio"]]} onChange={setOutput} />
            {output === "mp3" ? (
              <SelectField label="MP3 bitrate" value={bitrate} choices={bitrates} onChange={setBitrate} />
            ) : (
              <SelectField label="Resolution preference" value={quality} choices={Object.entries(qualities).sort(([a], [b]) =>
                a === "best" ? -1 : b === "best" ? 1 : Number(b) - Number(a))} onChange={setQuality} />
            )}
          </div>
          <small className="download-quality-hint">{output === "mp3"
            ? "The bitrate controls MP3 encoding quality."
            : "The available source resolution determines the actual output, up to 8K."}</small>
          <SourceRangeOptions value={sourceRange} download onChange={(next) => {
            setSourceRange((previous) => ({ ...previous, ...next }));
            setError("");
          }} />
          <details className="download-playlist-options">
            <summary>Playlist options</summary>
            <div className="download-format">
              <div>
                <label htmlFor="playlist-start">First item</label>
                <div className="input-wrap"><input id="playlist-start" type="number" min={1} max={1000000}
                  placeholder="1" value={playlistStart} onChange={(event) => setPlaylistStart(event.target.value)} /></div>
              </div>
              <div>
                <label htmlFor="playlist-end">Last item</label>
                <div className="input-wrap"><input id="playlist-end" type="number" min={Number(playlistStart || 1)}
                  max={Number(playlistStart || 1) + playlistLimit - 1} placeholder={`Next ${playlistLimit} items`}
                  value={playlistEnd} onChange={(event) => setPlaylistEnd(event.target.value)} /></div>
              </div>
            </div>
            <small className="download-quality-hint">Leave the last item blank to queue up to {playlistLimit} items from the starting position.</small>
            <label className="preference-check">
              <input type="checkbox" checked={linkedPlaylist} onChange={(event) => setLinkedPlaylist(event.target.checked)} />
              <span>Use the playlist when a YouTube video link includes one</span>
            </label>
          </details>
          <div className="download-submit">
            <button className="primary-button" type="submit" disabled={queueing || !links.length || links.length > batchLimit}>
              <ArrowDownToLine size={17} aria-hidden="true" /> {queueing ? "Adding…" : "Start download"}
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
