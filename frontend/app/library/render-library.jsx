"use client";

import { useEffect, useId, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Archive, CheckSquare, ChevronLeft, ChevronRight, Eraser, Film, HardDrive, LibraryBig, Plus, Search, SlidersHorizontal, Trash2 } from "lucide-react";
import { api, statusLabels, submit } from "../api";
import { editingStyles, SelectField } from "../render-options";

const statuses = { ...statusLabels, complete: "Ready", empty: "No matches", deleting: "Cleanup incomplete" };
const filters = {
  mode: [["", "All modes"], ["shorts", "Short Clips"], ["highlights", "Highlights"]],
  status: [["", "All statuses"], ["complete", "Ready"], ["active", "In progress"], ["failed", "Failed"],
    ["cancelled", "Cancelled"], ["empty", "No matches"], ["deleting", "Cleanup incomplete"]],
  age: [["", "Any date"], ["30", "Older than 30 days"], ["90", "Older than 90 days"], ["365", "Older than 1 year"]],
  sort: [["", "Newest first"], ["oldest", "Oldest first"], ["largest", "Largest first"]],
};
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const quickStatuses = [["", "All"], ["complete", "Ready"], ["active", "In progress"], ["failed", "Failed"]];

function formatBytes(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  const unit = bytes > 0 ? Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024))) : 0;
  const amount = bytes / 1024 ** unit;
  return `${amount.toLocaleString(undefined, { maximumFractionDigits: unit && amount < 100 ? 1 : 0 })} ${units[unit]}`;
}

function CleanupDialog({ renders, workOnly, onCancel, onDelete, busy, error }) {
  const dialog = useRef(null);
  const title = useId();
  const description = useId();
  useEffect(() => {
    const element = dialog.current;
    element.showModal();
    return () => element.close();
  }, []);
  const size = (render) => workOnly ? render.work_bytes : render.bytes;
  const bytes = renders.reduce((total, render) => total + size(render), 0);
  return (
    <dialog ref={dialog} className="library-dialog" aria-labelledby={title} aria-describedby={description}
      onCancel={(event) => { event.preventDefault(); if (!busy) onCancel(); }}>
      <div className="library-delete-icon">{workOnly ? <Eraser size={22} aria-hidden="true" /> : <Trash2 size={22} aria-hidden="true" />}</div>
      <h2 id={title}>{workOnly ? "Clear working files?" : `Delete ${renders.length === 1 ? "this render" : `${renders.length} renders`}?`}</h2>
      <p id={description}>{workOnly ? "This removes temporary media and downloaded source copies used during rendering." :
        "This permanently removes the local media, uploaded copies, and ZIP exports. Published videos stay on their platforms."}</p>
      <ul className="library-delete-items">
        {renders.map((render) => <li key={render.id}><span>{render.title}</span><strong>{formatBytes(size(render))}</strong></li>)}
      </ul>
      {error && <p className="field-error" role="alert">{error}</p>}
      <div className="library-dialog-actions">
        <strong>{formatBytes(bytes)} to reclaim</strong>
        <button className="secondary-button" type="button" autoFocus disabled={busy} onClick={onCancel}>Cancel</button>
        <button className="danger-button" type="button" disabled={busy} onClick={onDelete}>
          {busy ? "Removing files…" : workOnly ? "Clear working files" : renders.length === 1 ? "Delete render" : "Delete renders"}
        </button>
      </div>
    </dialog>
  );
}

function LibraryRow({ render, selecting, selected, onSelect, onCleanup }) {
  const reel = render.studio_mode === "highlights";
  const title = render.title;
  const date = dateFormat.format(new Date(render.created_at));
  const isActive = ["queued", "running", "cancelling"].includes(render.status);
  const isBlocked = Boolean(render.delete_blocked);
  const details = `/jobs/${render.id}`;
  return (
    <li className="library-row" data-selected={selected}>
      {selecting && <input className="library-check" type="checkbox" checked={selected} disabled={isBlocked}
        aria-label={`Select ${title}`} onChange={(event) => onSelect(render.id, event.target.checked)} />}
      <div className="library-cover">
        {render.thumbnail_url ? <Link href={details} tabIndex={-1} aria-hidden="true">
          <Image src={render.thumbnail_url} alt="" fill unoptimized sizes="96px" />
        </Link> : reel ? <LibraryBig size={22} aria-hidden="true" /> : <Film size={22} aria-hidden="true" />}
      </div>
      <div className="library-render">
        {render.status === "deleting" ? <strong>{title}</strong> : <Link href={details}>{title}</Link>}
        <span>{reel ? "Highlights" : "Short Clips"} · {render.aspect_ratio}{!reel && editingStyles[render.editing_style] &&
          ` · ${editingStyles[render.editing_style][0]}`}{render.clip_count > 0 && !reel &&
          ` · ${render.clip_count} ${render.clip_count === 1 ? "clip" : "clips"}`}</span>
        <span className="library-source">{render.source} · <time dateTime={render.created_at}>{date}</time></span>
      </div>
      <div className="library-state">
        <span className={`status ${render.status === "empty" ? "queued" : render.status === "deleting" ? "failed" : render.status}`}>
          {statuses[render.status]}{render.status === "running" && ` ${render.progress}%`}
        </span>
        <strong>{formatBytes(render.bytes)}</strong>
        {render.work_bytes > 0 && <small>{formatBytes(render.work_bytes)} working files</small>}
        {isBlocked && !isActive && <small>{render.source_in_use ? "Source in use" : "Publication pending"}</small>}
      </div>
      <div className="library-row-actions">
        {render.archive_url && <a href={render.archive_url} className="secondary-button" aria-label={`Download ZIP for ${title}`}>
          <Archive size={15} aria-hidden="true" /> ZIP</a>}
        {!selecting && render.work_bytes > 0 && <button type="button" className="secondary-button" title="Clear working files"
          aria-label={`Clear working files for ${title}`} onClick={() => onCleanup([render], true)}>
          <Eraser size={17} aria-hidden="true" />
        </button>}
        {!selecting && <button type="button" className="secondary-button library-delete-button" disabled={isBlocked}
          title={render.delete_blocked || "Delete render"} aria-label={`Delete ${title}`} onClick={() => onCleanup([render])}>
          <Trash2 size={17} aria-hidden="true" />
        </button>}
      </div>
    </li>
  );
}

export default function RenderLibrary() {
  const params = useSearchParams();
  const [draft, setDraft] = useState(params.get("q") || "");
  const [data, setData] = useState(null);
  const [filtersOpen, setFiltersOpen] = useState(() => Boolean(params.get("age") || params.get("mode") || params.get("sort")));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [cleanupError, setCleanupError] = useState("");
  const [notice, setNotice] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState([]);
  const [confirmation, setConfirmation] = useState(null);
  const [deleteError, setDeleteError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const query = params.toString();
  const status = params.get("status") || "";
  const extraFilters = ["age", "mode", "sort"].filter((key) => params.get(key)).length +
    Number(Boolean(status && !quickStatuses.some(([value]) => value === status)));
  const hasFilters = Boolean(params.get("q") || status || extraFilters);
  const current = data?.query === query ? data : null;
  const renders = current?.renders ?? [];
  const deletable = renders.filter((render) => !render.delete_blocked);
  const checked = deletable.filter((render) => selected.includes(render.id));
  const workSelection = checked.filter((render) => render.work_bytes > 0);
  const pageBytes = renders.reduce((total, render) => total + render.bytes, 0);

  useEffect(() => { setDraft(params.get("q") || ""); }, [query, params]);

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    async function load(background = false) {
      if (!background) { setLoading(true); setError(""); }
      try {
        const result = await api(`library/?${query}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setData({ ...result, query });
        setError("");
        if (result.renders.some((render) => ["queued", "running", "cancelling"].includes(render.status))) {
          timer = setTimeout(() => load(true), 6000);
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure.message);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [query, attempt]);

  function navigate(changes, reset = false) {
    const next = new URLSearchParams(reset ? "" : query);
    if (!("page" in changes)) next.delete("page");
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    const search = next.toString();
    if (query === search) return;
    setSelected([]);
    window.history.pushState(null, "", `/library${search ? `?${search}` : ""}`);
  }

  function openCleanup(renders, workOnly = false) {
    setDeleteError("");
    setConfirmation({ renders, workOnly });
  }

  async function remove() {
    if (!confirmation || deleting) return;
    setDeleting(true);
    setDeleteError("");
    try {
      const { renders, workOnly } = confirmation;
      const result = await submit(workOnly ? "library/clear-work/" : "library/delete/", { ids: renders.map((render) => render.id) });
      const { removed, blocked } = result;
      const removedBytes = new Map(removed.map(({ id, bytes_freed }) => [id, bytes_freed]));
      const bytes = removed.reduce((total, item) => total + item.bytes_freed, 0);
      setSelected([]);
      setData((previous) => ({ ...previous, renders: workOnly ? previous.renders.map((render) => removedBytes.has(render.id) ?
        { ...render, work_bytes: 0, bytes: Math.max(0, render.bytes - removedBytes.get(render.id)) } : render) :
        previous.renders.filter((render) => !removedBytes.has(render.id)) }));
      setNotice(removed.length ? `${workOnly ? "Working files cleared" :
        `Deleted ${removed.length} ${removed.length === 1 ? "render" : "renders"}`} · ${formatBytes(bytes)} reclaimed` : "");
      setCleanupError(blocked.map(({ id, reason }) => `${renders.find((render) => render.id === id)?.title || "Render"} · ${reason}`).join(" "));
      setConfirmation(null);
      setAttempt((value) => value + 1);
    } catch (failure) {
      setDeleteError(failure.message);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section className="render-library" aria-labelledby="library-title">
      <header className="page-heading">
        <div><h1 id="library-title">Library</h1><p>Find finished clips, check render progress, and manage storage.</p></div>
        <Link className="primary-button" href="/"><Plus size={18} aria-hidden="true" /> New render</Link>
      </header>
      <div className="panel library-panel">
        <div className="library-tools">
          <div className="library-toolbar">
            <form className="library-search" role="search" onSubmit={(event) => {
              event.preventDefault(); navigate({ q: draft.trim() });
            }}>
              <div className="input-wrap"><Search size={17} aria-hidden="true" />
                <input type="search" aria-label="Search renders by title or source" maxLength={180} placeholder="Search renders" value={draft}
                  onChange={(event) => setDraft(event.target.value)} /></div>
              <button type="submit" className="secondary-button">Search</button>
            </form>
            <button className="secondary-button library-filter-toggle" type="button" aria-expanded={filtersOpen}
              aria-controls="library-advanced-filters" onClick={() => setFiltersOpen(!filtersOpen)}>
              <SlidersHorizontal size={16} aria-hidden="true" /> Filters {extraFilters > 0 && <span>{extraFilters}</span>}
            </button>
          </div>
          <div className="library-quick-filters" role="group" aria-label="Filter by render status">
            {quickStatuses.map(([value, label]) => <button type="button" key={value} aria-pressed={status === value}
              onClick={() => navigate({ status: value })}>{label}</button>)}
          </div>
          <div className="library-filters" id="library-advanced-filters" hidden={!filtersOpen}>
            {[["mode", "Mode"], ["status", "Status"], ["age", "Age"], ["sort", "Sort by"]].map(([key, label]) => (
              <SelectField key={key} label={label} choices={filters[key]}
                value={filters[key].some(([value]) => value === params.get(key)) ? params.get(key) : ""}
                onChange={(value) => navigate({ [key]: value })} />
            ))}
          </div>
        </div>
        <div className="library-summary">
          <p aria-live="polite">{current ? <><strong>{current.total.toLocaleString()} {current.total === 1 ? "render" : "renders"}</strong>
            <span><HardDrive size={15} aria-hidden="true" /> {formatBytes(pageBytes)} on this page</span></> : loading ? "Loading renders…" : "Renders unavailable"}</p>
          <div>
            {hasFilters && <button className="secondary-button" type="button" onClick={() => navigate({}, true)}>Clear filters</button>}
            {renders.length > 0 && <button aria-pressed={selecting} className="secondary-button" type="button"
              onClick={() => { setSelecting(!selecting); setSelected([]); }}>
              <CheckSquare size={16} aria-hidden="true" /> {selecting ? "Done selecting" : "Select renders"}
            </button>}
          </div>
        </div>
        {selecting && renders.length > 0 && <div className="library-bulk">
          <label><input className="library-check" type="checkbox" disabled={loading || !deletable.length}
            checked={Boolean(deletable.length && checked.length === deletable.length)}
            onChange={(event) => setSelected(event.target.checked ? deletable.map((render) => render.id) : [])} />
            Select this page</label>
          <span>{checked.length} selected · {formatBytes(checked.reduce((total, render) => total + render.bytes, 0))}</span>
          {workSelection.length > 0 && <button className="secondary-button" type="button" disabled={loading} onClick={() => openCleanup(workSelection, true)}>
            <Eraser size={15} aria-hidden="true" /> Clear working files</button>}
          <button className="danger-button" type="button" disabled={loading || !checked.length} onClick={() => openCleanup(checked)}>
            <Trash2 size={15} aria-hidden="true" /> Delete selected</button>
        </div>}
        {notice && <p className="library-notice" role="status">{notice}</p>}
        {(cleanupError || error) && <p className="library-error field-error" role="alert">{cleanupError || error}</p>}
        {!loading && error && <button className="secondary-button library-retry" type="button"
          onClick={() => setAttempt((value) => value + 1)}>Try again</button>}
        <div aria-busy={loading}>
          {!current && loading && <div className="library-loading" aria-hidden="true"><i /><i /><i /></div>}
          {renders.length ? <ul className="library-list">
            {renders.map((render) => <LibraryRow key={render.id} render={render} selecting={selecting}
              selected={selected.includes(render.id)} onCleanup={openCleanup}
              onSelect={(id, checked) => setSelected((current) => checked ? [...current, id] : current.filter((item) => item !== id))} />)}
          </ul> : !loading && current && <div className="library-empty">
            <LibraryBig size={26} aria-hidden="true" />
            <h2>{hasFilters ? "No renders match your filters" : "No renders yet"}</h2>
            {hasFilters ? <button className="secondary-button" type="button" onClick={() => navigate({}, true)}>Clear filters</button> :
              <Link className="secondary-button" href="/">Create a render</Link>}
          </div>}
        </div>
        {current?.pages > 1 && <nav className="library-pagination" aria-label="Library pages">
          <button className="secondary-button" type="button" disabled={loading || current.page <= 1}
            onClick={() => navigate({ page: current.page - 1 })}><ChevronLeft size={17} aria-hidden="true" /> Previous</button>
          <span>Page {current.page} of {current.pages}</span>
          <button className="secondary-button" type="button" disabled={loading || current.page >= current.pages}
            onClick={() => navigate({ page: current.page + 1 })}>Next <ChevronRight size={17} aria-hidden="true" /></button>
        </nav>}
      </div>
      {confirmation && <CleanupDialog {...confirmation} busy={deleting} error={deleteError}
        onCancel={() => setConfirmation(null)} onDelete={remove} />}
    </section>
  );
}
