"use client";

import Image from "next/image";
import Link from "next/link";
import { useId, useMemo, useRef, useState } from "react";
import { Check, ChevronRight, Film, Folder, FolderOpen, Folders, Search, X } from "lucide-react";

const renderDate = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function videoDuration(clip) {
  const duration = clip.duration_seconds ?? (clip.end_time - clip.start_time);
  if (!Number.isFinite(duration) || duration <= 0) return "";
  const seconds = Math.max(1, Math.round(duration));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds / 60) % 60;
  const remainder = String(seconds % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${remainder}` : `${minutes}:${remainder}`;
}

function renderName(job) {
  return job.source_name || job.clips[0].title || "YouTube render";
}

function matchesSearch(value, search) {
  return value.toLocaleLowerCase().includes(search);
}

function moveFocus(event) {
  if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
  const rows = Array.from(event.currentTarget.querySelectorAll("button:not(:disabled), input:not(:disabled)"));
  const current = rows.indexOf(event.target);
  if (current < 0) return;
  const index = event.key === "Home" ? 0 : event.key === "End" ? rows.length - 1 :
    current + (event.key === "ArrowUp" ? -1 : 1);
  event.preventDefault();
  rows[Math.min(rows.length - 1, Math.max(0, index))].focus();
}

export default function MediaBrowser({ jobs, loadError, onRetry, initialJob, selected, onPick, checked, onToggle,
  onSelectShown, onClear, limit = 0, disabled }) {
  const field = useId();
  const searchField = useRef(null);
  const [renderId, setRenderId] = useState(initialJob || "");
  const [query, setQuery] = useState("");
  const bulk = Boolean(checked);
  const search = query.trim().toLocaleLowerCase();
  const renders = useMemo(() => (jobs || []).map((job) => {
    const showAll = matchesSearch([renderName(job), job.video_id].join(" "), search);
    const clips = job.clips.map((clip, index) => ({ clip, index, job, key: `${job.id}:${index}` }));
    return { job, clips: showAll ? clips : clips.filter(({ clip }) => matchesSearch(clip.title || "", search)) };
  }).filter(({ clips }) => clips.length), [jobs, search]);
  const all = bulk && renderId === "all" && renders.length > 1;
  const active = renders.find(({ job }) => job.id === renderId) || renders[0];
  const shown = all ? renders.flatMap(({ clips }) => clips) : active?.clips || [];
  const selectedCount = bulk ? Object.keys(checked).length : 0;
  const choices = bulk ? shown.filter(({ key }) => !checked[key]) : [];
  const room = limit - selectedCount;

  function clearSearch() {
    setQuery("");
    searchField.current?.focus();
  }

  function renderButton(id, title, caption, Icon, current) {
    return (
      <button type="button" className="media-browser-row media-render-row" aria-pressed={current} disabled={disabled}
        onClick={() => setRenderId(id)}>
        <span className="media-render-icon"><Icon size={18} aria-hidden="true" /></span>
        <span className="media-row-text"><strong title={title}>{title}</strong><small>{caption}</small></span>
        <ChevronRight className="media-render-chevron" size={15} aria-hidden="true" />
      </button>
    );
  }

  return (
    <section className="panel media-browser" aria-label={bulk ? "Browse videos for the batch" : "Choose a video to publish"}>
      <div className="media-browser-toolbar">
        <div className="media-browser-search">
          <label className="sr-only" htmlFor={`${field}-search`}>Find a render or clip</label>
          <Search size={17} aria-hidden="true" />
          <input id={`${field}-search`} ref={searchField} type="search" placeholder="Find a render or clip" value={query}
            disabled={!jobs?.length || disabled} onChange={(event) => setQuery(event.target.value)} />
          {query && <button type="button" aria-label="Clear video search" disabled={disabled} onClick={clearSearch}>
            <X size={15} aria-hidden="true" />
          </button>}
        </div>
        {bulk && <div className="media-browser-tools">
          <span role="status">{selectedCount} selected</span>
          <button type="button" onClick={() => onSelectShown(choices)} disabled={disabled || !choices.length || room <= 0}>
            {room > 0 && choices.length > room ? `Select first ${room}` : "Select shown"}
          </button>
          {selectedCount > 0 && <button type="button" onClick={onClear} disabled={disabled}>Clear selection</button>}
        </div>}
      </div>

      {!renders.length ? <div className="media-browser-empty">
        <span className="media-render-icon"><FolderOpen size={22} aria-hidden="true" /></span>
        {loadError ? <><p role="alert">{loadError}</p><button type="button" className="secondary-button" onClick={onRetry}>Try again</button></> :
          !jobs ? <p role="status">Loading finished renders…</p> : jobs.length ? <>
            <p>No renders or clips match your search.</p><button type="button" className="secondary-button" onClick={clearSearch}>Clear search</button>
          </> : <><p>Your finished renders will appear here.</p><Link className="secondary-button" href="/">Create a render</Link></>}
      </div> : (
        <div className="media-browser-panes">
          <section className="media-browser-pane" aria-labelledby={`${field}-renders`}>
            <div className="media-pane-header">
              <h2 id={`${field}-renders`}>Renders</h2><span>{renders.length}</span>
            </div>
            <ul className="media-pane-list" onKeyDown={moveFocus}>
              {bulk && renders.length > 1 && <li>{renderButton("all", "All renders", "Browse clips across renders", Folders, all)}</li>}
              {renders.map(({ job, clips }) => {
                const current = !all && active?.job.id === job.id;
                const count = job.clips.length;
                const picked = bulk ? job.clips.reduce((total, _, index) => total + Number(Boolean(checked[`${job.id}:${index}`])), 0) : 0;
                const detail = [search && clips.length !== count ? `${clips.length} of ${count} shown` : `${count} ${count === 1 ? "video" : "videos"}`,
                  picked ? `${picked} selected` : job.created_at && renderDate.format(new Date(job.created_at))].filter(Boolean).join(" · ");
                return <li key={job.id}>{renderButton(job.id, renderName(job), detail, current ? FolderOpen : Folder, current)}</li>;
              })}
            </ul>
          </section>
          <section className="media-browser-pane" aria-labelledby={`${field}-clips`}>
            <div className="media-pane-header">
              <h2 id={`${field}-clips`}>Clips</h2><span>{shown.length}</span>
            </div>
            <ul className="media-pane-list" key={all ? "all" : active?.job.id} onKeyDown={moveFocus}>
              {shown.map(({ clip, job, index, key }) => {
                const current = bulk ? Boolean(checked[key]) : selected === key;
                const description = [job.studio_mode === "highlights" ? "Highlight reel" : "Short clip", videoDuration(clip),
                  all ? renderName(job) : ""].filter(Boolean).join(" · ");
                const content = <>
                  <span className="media-clip-cover">{clip.thumbnail_url ?
                    <Image unoptimized fill src={clip.thumbnail_url} alt="" sizes="42px" /> : <Film aria-hidden="true" size={16} />}</span>
                  <span className="media-row-text"><strong title={clip.title}>{clip.title}</strong><small>{description}</small></span>
                </>;
                return <li key={key}>{bulk ? (
                  <label className="media-browser-row media-clip-row" data-current={current}>
                    <input type="checkbox" aria-label={`Select ${clip.title}`} checked={current} disabled={disabled || (!current && room <= 0)}
                      onChange={(event) => onToggle({ key, clip, job, index }, event.target.checked)} />{content}
                  </label>
                ) : (
                  <button type="button" className="media-browser-row media-clip-row" aria-pressed={current}
                    disabled={disabled} onClick={() => onPick(key)}>
                    {content}{current && <Check className="media-clip-check" size={16} aria-hidden="true" />}
                  </button>
                )}</li>;
              })}
            </ul>
          </section>
        </div>
      )}
    </section>
  );
}
