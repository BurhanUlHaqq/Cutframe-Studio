"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { CalendarClock, Check, ChevronDown, ChevronLeft, ChevronRight, Clipboard, Download, ExternalLink, Film, Layers, Link2, Music2, Send } from "lucide-react";
import { api, submit } from "../api";
import { SelectField } from "../render-options";
import BulkComposer from "./bulk-composer";
import MediaBrowser, { videoDuration } from "./media-browser";
import { platforms } from "./platforms";
import TikTokStudio from "./tiktok-studio";

const tabs = [["compose", "New post", Send], ["bulk", "Bulk post", Layers], ["tiktok", "TikTok Studio", Music2],
  ["schedule", "Queue", CalendarClock], ["accounts", "Accounts", Link2]];
const initialTabs = { bulk: "bulk", queue: "schedule", tiktok: "tiktok" };
const statuses = {
  queued: "Queued", sending: "Sending", processing: "Processing", inbox: "Ready in TikTok",
  complete: "Complete", failed: "Failed", review: "Check platform", cancelled: "Cancelled",
};
const queueStatuses = [["", "All statuses"], ["active", "Queued or in progress"], ["attention", "Needs attention"],
  ["complete", "Complete"], ["inbox", "Ready in TikTok"], ["cancelled", "Cancelled"]];
const queuePlatforms = [["", "All platforms"], ...Object.entries(platforms).map(([key, platform]) => [key, platform.name])];

function errorText(error) {
  return Object.values(error.fields || {})[0]?.[0]?.message || error.message;
}

function targetKey(platform, id) {
  return `${platform}:${id}`;
}

const initialDraft = { destination: "", title: "", caption: "", privacy: "private", made_for_kids: "", synthetic: false, scheduled: "", confirm: false };

export default function PublishStudio({ initialJob, initialClip, initialMode, initialBatch }) {
  const [tab, setTab] = useState(initialBatch ? "schedule" : initialTabs[initialMode] || "compose");
  const [queueView, setQueueView] = useState({ batch: initialBatch, page: 1, status: "", platform: "" });
  const [queueData, setQueueData] = useState(null);
  const [queueError, setQueueError] = useState("");
  const [jobs, setJobs] = useState(null);
  const [jobsError, setJobsError] = useState("");
  const [connections, setConnections] = useState(null);
  const [selected, setSelected] = useState("");
  const [draft, setDraft] = useState(initialDraft);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const requestId = useRef(null);
  const postText = useRef(new Map());
  const queueVersion = useRef(0);
  const queueHeading = useRef(null);
  const { batch, status: queueStatus, platform: queuePlatform } = queueView;
  const filteredQueue = Boolean(queueStatus || queuePlatform);
  const currentQueue = queueData?.view === queueView ? queueData : null;
  const pagination = queueData?.view.batch === batch ? queueData.pagination : null;
  const loadingQueue = !currentQueue && !queueError;
  const singleVideo = tab === "compose" || tab === "tiktok";
  const browseMedia = singleVideo || tab === "bulk";
  const needsAccounts = tab !== "tiktok";

  const reloadAccounts = useCallback(async () => setConnections(await api("publishing/accounts/")), []);
  const reloadPosts = useCallback(async ({ signal, background = false } = {}) => {
    const version = ++queueVersion.current;
    const params = new URLSearchParams({ page: queueView.page });
    if (queueView.batch) params.set("batch", queueView.batch);
    if (queueView.status) params.set("status", queueView.status);
    if (queueView.platform) params.set("platform", queueView.platform);
    if (!background) setQueueError("");
    try {
      const result = await api(`publishing/posts/?${params}`, { signal });
      if (!signal?.aborted && version === queueVersion.current) {
        setQueueData({ ...result, view: queueView });
        setQueueError("");
      }
    } catch (failure) {
      if (!background && !signal?.aborted && version === queueVersion.current) setQueueError(errorText(failure));
    }
  }, [queueView]);

  useEffect(() => {
    if (!browseMedia || jobs || jobsError) return;
    const controller = new AbortController();
    async function loadClips() {
      const { jobs } = await api("jobs/?finished=1", { signal: controller.signal });
      if (controller.signal.aborted) return;
      setJobs(jobs);
      const match = jobs.find((job) => job.id === initialJob);
      if (!match) return;
      const requested = /^\d+$/.test(initialClip) ? Number(initialClip) : 0;
      const index = match.clips[requested] ? requested : 0;
      setSelected(`${match.id}:${index}`);
      setDraft((previous) => ({ ...previous, title: match.clips[index].title, caption: match.clips[index].description }));
    }
    loadClips().catch((failure) => { if (!controller.signal.aborted) setJobsError(errorText(failure)); });
    return () => controller.abort();
  }, [browseMedia, initialJob, initialClip, jobs, jobsError]);

  function retryVideos() {
    setJobsError("");
  }

  useEffect(() => {
    if (!needsAccounts || connections) return;
    let ignore = false;
    reloadAccounts().catch((failure) => { if (!ignore) setError(errorText(failure)); });
    return () => { ignore = true; };
  }, [connections, needsAccounts, reloadAccounts]);

  useEffect(() => {
    if (tab !== "schedule") return;
    const controller = new AbortController();
    let timer;
    async function refresh(background = false) {
      if (!background || document.visibilityState === "visible") {
        await reloadPosts({ signal: controller.signal, background });
      }
      if (!controller.signal.aborted) timer = setTimeout(() => refresh(true), 5000);
    }
    refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [reloadPosts, tab]);

  useEffect(() => {
    const result = new URLSearchParams(window.location.hash.slice(1));
    if (!result.has("connection") && !result.has("state")) return;
    window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    setTab("accounts");
    if (!result.has("code")) {
      setError(result.get("connection") === "cancelled" ? "Authorization was cancelled." : "This connection expired. Start again below.");
      return;
    }
    setBusy("connect");
    submit("publishing/finish/", { platform: result.get("provider"), state: result.get("state"), code: result.get("code") })
      .then(async (data) => {
        await reloadAccounts();
        setMessage(`${data.connected} connected.`);
      })
      .catch((failure) => setError(errorText(failure)))
      .finally(() => setBusy(""));
  }, [reloadAccounts]);

  const destinations = (connections?.platforms || []).flatMap((platform) =>
    platform.configured ? platform.accounts.map((account) => ({ ...account, platform: platform.platform })) : []);
  const destination = destinations.find((entry) => targetKey(entry.platform, entry.id) === draft.destination);
  const [jobId, clipIndex] = selected.split(":");
  const job = jobs?.find((entry) => entry.id === jobId);
  const clip = job?.clips[Number(clipIndex)];
  const reel = job?.studio_mode === "highlights";
  const youtube = destination?.platform === "youtube";
  const tiktok = destination?.platform === "tiktok";
  const instagram = destination?.platform === "instagram";

  function show(next) {
    setError("");
    setMessage("");
    setTab(next);
  }

  function showQueue(batchId, page = 1, filters = {}) {
    queueVersion.current += 1;
    setQueueError("");
    setQueueView({ batch: batchId, page, status: "", platform: "", ...filters });
  }

  function goToQueuePage(page) {
    showQueue(batch, page, { status: queueStatus, platform: queuePlatform });
    queueHeading.current?.scrollIntoView({ block: "start" });
    queueHeading.current?.focus({ preventScroll: true });
  }

  function change(fields) {
    requestId.current = null;
    setError("");
    setDraft((previous) => ({ ...previous, confirm: false, ...fields }));
  }

  function pick(key) {
    if (key === selected) return;
    const [id, index] = key.split(":");
    const choice = jobs.find((entry) => entry.id === id)?.clips[Number(index)];
    if (!choice) return;
    if (selected) postText.current.set(selected, { title: draft.title, caption: draft.caption });
    setSelected(key);
    setMessage("");
    change(postText.current.get(key) || { title: choice.title || "", caption: choice.description || "" });
  }

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      setMessage("Copied.");
    } catch {
      setError("Could not copy to the clipboard. Select and copy the text manually.");
    }
  }

  async function perform(key, operation) {
    setBusy(key);
    setError("");
    setMessage("");
    try {
      await operation();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy("");
    }
  }

  function queue(event) {
    event.preventDefault();
    if (!destination || !draft.confirm || !clip || busy) return;
    perform("queue", async () => {
      requestId.current ||= crypto.randomUUID();
      await submit("publishing/posts/", {
        request_id: requestId.current, job_id: jobId, clip_index: Number(clipIndex),
        platform: destination.platform, account_id: destination.id, title: draft.title, caption: draft.caption,
        privacy: youtube ? draft.privacy : "", made_for_kids: youtube ? draft.made_for_kids : "",
        synthetic: draft.synthetic, scheduled_at: draft.scheduled ? new Date(draft.scheduled).toISOString() : null,
        confirm: true,
      });
      showQueue("");
      requestId.current = null;
      setDraft((previous) => ({ ...previous, confirm: false }));
      setTab("schedule");
      setMessage(draft.scheduled ? "Post scheduled." : "Clip queued for transfer.");
    });
  }

  function queueBulk(result, scheduled) {
    showQueue(result.batch_id);
    setTab("schedule");
    const count = result.posts.length;
    setMessage(`${count} ${count === 1 ? "post" : "posts"} ${scheduled ? "scheduled" : "queued for transfer"}.`);
  }

  return (
    <section className="publish-page" aria-labelledby="publish-title">
      <header className="page-heading">
        <div><h1 id="publish-title">Publish Studio</h1>
          <p>Prepare finished clips and reels for YouTube, TikTok, and Instagram Reels.</p></div>
      </header>
      <div className="publish-tabs" role="group" aria-label="Publish Studio sections">
        {tabs.map(([key, label, Icon]) => (
          <button type="button" key={key} aria-pressed={tab === key} disabled={Boolean(busy)} onClick={() => show(key)}>
            <Icon size={17} aria-hidden="true" />{label}
          </button>
        ))}
      </div>
      {error && <p className="field-error publish-notice" role="alert">{error}</p>}
      {message && <p className="settings-saved publish-notice" role="status"><Check size={17} aria-hidden="true" />{message}</p>}
      {tab === "bulk" && <BulkComposer jobs={jobs} loadError={jobsError} onRetry={retryVideos} destinations={destinations} initialJob={initialJob}
        onAccounts={() => show("accounts")} onQueue={queueBulk} setParentBusy={(active) => setBusy(active ? "bulk" : "")} />}
      {tab === "accounts" && (
        <section className="publish-accounts" aria-label="Publishing accounts">
          <article className="panel settings-panel publish-account tiktok-browser-option">
            <div className="panel-heading">
              <span className="heading-icon"><Music2 size={20} aria-hidden="true" /></span>
              <div><h2>TikTok in your browser</h2><p>Use the account you sign in to on TikTok Studio. No developer app required.</p></div>
            </div>
            <button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => show("tiktok")}>
              Go to TikTok Studio tools <ChevronRight size={16} aria-hidden="true" />
            </button>
          </article>
          {!connections && <p className="muted">Loading connections…</p>}
          {connections?.platforms.map((entry) => {
            const platform = platforms[entry.platform];
            const connected = entry.accounts.length > 0;
            const callback = entry.redirect_uri;
            const inbox = entry.platform === "tiktok";
            return (
              <article className="panel settings-panel publish-account" key={entry.platform}>
                <div className="panel-heading">
                  <span className="heading-icon"><platform.Icon size={21} aria-hidden="true" /></span>
                  <div><h2>{inbox ? "TikTok inbox drafts" : platform.name}</h2>
                    <p>{connected ? entry.accounts.map(({ label }) => label).join(", ") : entry.configured ? "Ready to connect" : "Setup required"}</p></div>
                </div>
                <p className="muted">{platform.note}</p>
                <div className="publish-buttons">
                  <button type="button" className="primary-button" disabled={!entry.configured || Boolean(busy)}
                    onClick={() => perform("connect", async () => {
                      const result = await submit(`publishing/connect/${entry.platform}/`, {});
                      window.location.assign(result.url);
                    })}>{connected ? "Reconnect" : "Connect account"}</button>
                  {connected && <button type="button" className="secondary-button" disabled={Boolean(busy)}
                    onClick={() => {
                      if (window.confirm(`Disconnect ${platform.name} and cancel its queued transfers?`)) perform("disconnect", async () => {
                        await submit(`publishing/disconnect/${entry.platform}/`, {});
                        await Promise.all([reloadAccounts(), reloadPosts()]);
                        setMessage(`${platform.name} disconnected.`);
                      });
                    }}>Disconnect</button>}
                </div>
                <details className="advanced publish-guide" open={!entry.configured ? true : undefined}>
                  <summary>Setup guide <ChevronDown size={16} aria-hidden="true" /></summary>
                  <ol>{platform.instructions.map((step) => <li key={step}>{step}</li>)}</ol>
                  <p>{platform.credentialLocation} Save the values in Settings → Publishing, then return here to connect.</p>
                  {!entry.redirect_uri && <p>Save a public HTTPS callback origin to generate the exact redirect URI.</p>}
                  {callback && <code className="publish-callback">{callback}</code>}
                  <div className="publish-buttons">
                    <Link className="secondary-button" href="/settings#publishing-settings-title">Open publishing settings</Link>
                    <a className="secondary-button" href={platform.dashboard} target="_blank" rel="noreferrer">{platform.dashboardLabel} <ExternalLink size={14} aria-hidden="true" /></a>
                    {callback && <button className="secondary-button" type="button" onClick={() => copy(callback)}><Clipboard size={15} aria-hidden="true" /> Copy callback</button>}
                    <a className="secondary-button" href={platform.docs} target="_blank" rel="noreferrer">Platform docs <ExternalLink size={14} aria-hidden="true" /></a>
                  </div>
                </details>
              </article>
            );
          })}
        </section>
      )}
      {singleVideo && (
        <div className="publish-compose">
          <div className="publish-workbench">
            <MediaBrowser jobs={jobs} loadError={jobsError} onRetry={retryVideos} initialJob={jobId || initialJob}
              selected={selected} onPick={pick} disabled={Boolean(busy)} />
            <section className="panel publish-clip" aria-labelledby="publish-clip-title">
              <div className="media-pane-header"><h2 id="publish-clip-title">Preview</h2></div>
              {clip ? <div className="publish-preview-body">
                <video className="publish-preview" key={clip.video_url} controls playsInline preload="metadata"
                  src={clip.video_url} poster={clip.thumbnail_url} aria-label={clip.title} />
                <div className="publish-preview-details" aria-live="polite">
                  <p>{clip.title}</p>
                  <small>{[reel ? "Highlight reel" : "Short clip", videoDuration(clip), job.aspect_ratio].filter(Boolean).join(" · ")}</small>
                  <div className="publish-buttons">
                    <a className="secondary-button" href={`${clip.video_url}?download=1`}><Download size={15} aria-hidden="true" /> MP4</a>
                    <Link className="secondary-button" href={`/jobs/${job.id}`}>Open render</Link>
                  </div>
                </div>
              </div> : <div className="media-browser-empty publish-preview-empty">
                <span className="media-render-icon"><Film size={22} aria-hidden="true" /></span><p>Select a clip to preview it.</p>
              </div>}
            </section>
          </div>
          {tab === "tiktok" && <TikTokStudio key={selected} clip={clip} caption={draft.caption || ""}
            onCaption={(caption) => change({ caption })} />}
          {tab === "compose" && <form className="panel settings-panel publish-form" onSubmit={queue}>
            <div className="panel-heading">
              <span className="heading-icon"><Send size={20} aria-hidden="true" /></span><h2>Post details</h2>
            </div>
            {!destinations.length && <div className="publish-connect-hint">
              <p>Connect an account to send videos directly from Cutframe.</p>
              <button type="button" className="secondary-button" onClick={() => show("accounts")}>Set up accounts</button>
            </div>}
            <fieldset className="control-group publish-fields publish-editor-fields" disabled={!clip || Boolean(busy)}>
              <legend className="sr-only">Post details</legend>
              <div className="publish-copy-fields">
                <div><label htmlFor="publish-title-field">{youtube ? "YouTube title" : "Title"}</label>
                  <div className="input-wrap"><input id="publish-title-field" required maxLength={100} value={draft.title}
                    onChange={(event) => change({ title: event.target.value })} /></div>
                </div>
                <div><label htmlFor="publish-caption">{youtube ? "Description" : "Caption"}</label>
                  <textarea id="publish-caption" value={draft.caption} rows={4} maxLength={instagram ? 2200 : 5000}
                    onChange={(event) => change({ caption: event.target.value })} />
                  {tiktok && <small>Copy this caption into TikTok when you finish the inbox draft.</small>}
                  {youtube && clip?.chapter_text && <small>Eligible chapter timestamps will be appended to the YouTube description.</small>}
                  {youtube && reel && clip.end_time > 900 && <small>YouTube requires channel verification for uploads longer than 15 minutes.</small>}
                </div>
              </div>
              <div className="publish-delivery-fields">
                <div><label htmlFor="publish-to">Destination</label>
                  <div className="select-wrap">
                    <select id="publish-to" required value={draft.destination} onChange={(event) => change({ destination: event.target.value })}>
                      <option value="">Choose a connected account</option>
                      {destinations.map((entry) => <option key={targetKey(entry.platform, entry.id)} value={targetKey(entry.platform, entry.id)}>
                        {platforms[entry.platform].name} · {entry.label}
                      </option>)}
                    </select>
                    <ChevronDown size={16} aria-hidden="true" />
                  </div>
                  {youtube && <small>YouTube gets the saved JPG after it finishes processing the video.</small>}
                  {instagram && <small>Instagram uses the saved thumbnail’s video frame as the cover when available.</small>}
                  {tiktok && <small>Choose a cover in TikTok when you finish the inbox draft.</small>}
                </div>
                {youtube && <div className="publish-options">
                  <div><label htmlFor="publish-privacy">YouTube visibility</label>
                    <div className="select-wrap"><select id="publish-privacy" value={draft.privacy}
                      onChange={(event) => change({ privacy: event.target.value })}>
                      <option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option>
                    </select><ChevronDown size={16} aria-hidden="true" /></div>
                  </div>
                  <div><label htmlFor="publish-audience">Made for kids</label>
                    <div className="select-wrap"><select id="publish-audience" required value={draft.made_for_kids}
                      onChange={(event) => change({ made_for_kids: event.target.value })}>
                      <option value="">Choose audience</option><option value="false">No</option><option value="true">Yes</option>
                    </select><ChevronDown size={16} aria-hidden="true" /></div>
                  </div>
                </div>}
                {destination && !tiktok && <label className="publish-check">
                  <input type="checkbox" checked={draft.synthetic} onChange={(event) => change({ synthetic: event.target.checked })} />
                  Disclose AI-generated or altered media
                </label>}
                <div><label htmlFor="publish-scheduled">Send time</label>
                  <div className="input-wrap"><input id="publish-scheduled" type="datetime-local" value={draft.scheduled}
                    onChange={(event) => change({ scheduled: event.target.value })} /></div>
                  <small>Uses your browser’s time zone. Leave blank to send as soon as the worker is ready.</small>
                </div>
                {destination && <label className="publish-check">
                  <input type="checkbox" required checked={draft.confirm} onChange={(event) => change({ confirm: event.target.checked })} />
                  I approve {tiktok ? "sending this draft to" : youtube ? "uploading this video to" : "publishing this Reel for"} {destination.label} on {platforms[destination.platform].name}.
                </label>}
              </div>
            </fieldset>
            <div className="settings-actions">
              <div className="publish-buttons">
                {clip && <button type="button" className="secondary-button" onClick={() =>
                  copy([draft.title, draft.caption, (!destination || youtube) && clip.chapter_text].filter(Boolean).join("\n\n"))}>
                  <Clipboard size={16} aria-hidden="true" /> Copy post text
                </button>}
              </div>
              <button type="submit" className="primary-button" disabled={!clip || !destination || !draft.confirm || Boolean(busy)}>
                <Send size={17} aria-hidden="true" />{busy === "queue" ? "Queuing…" : draft.scheduled ? "Schedule send" : tiktok ? "Send to TikTok inbox" : "Queue post"}
              </button>
            </div>
          </form>}
          {tab === "compose" && <details className="panel settings-panel publish-manual">
            <summary>Post manually <ChevronDown size={16} aria-hidden="true" /></summary>
            <p className="muted">Download the MP4 and copy the post text. Open your platform to upload it.</p>
            <div className="publish-buttons">{[platforms.youtube, platforms.instagram].map((platform) => (
              <a key={platform.name} className="secondary-button" href={platform.upload} target="_blank" rel="noreferrer">
                {platform.name} <ExternalLink size={14} aria-hidden="true" />
              </a>
            ))}<button type="button" className="secondary-button" onClick={() => show("tiktok")}>
              TikTok Studio <ChevronRight size={16} aria-hidden="true" />
            </button></div>
          </details>}
        </div>
      )}
      {tab === "schedule" && (
        <section className="panel settings-panel publish-queue" aria-labelledby="publish-queue-title">
          <div className="panel-heading">
            <span className="heading-icon"><CalendarClock size={20} aria-hidden="true" /></span>
            <div><h2 id="publish-queue-title" ref={queueHeading} tabIndex={-1}>{batch ? "Batch queue" : "Publishing queue"}</h2>
              {currentQueue && pagination.total > 0 && <p role="status">Showing {pagination.start === pagination.end ? pagination.start :
                `${pagination.start}–${pagination.end}`} of {pagination.total} {pagination.total === 1 ? "post" : "posts"}</p>}</div>
            {batch && <button type="button" className="secondary-button queue-all-posts" disabled={Boolean(busy)}
              onClick={() => showQueue("")}>All posts</button>}
          </div>
          <fieldset className="control-group queue-filters" disabled={Boolean(busy)}>
            <legend className="sr-only">Filter publishing queue</legend>
            <SelectField label="Status" value={queueStatus} choices={queueStatuses}
              onChange={(status) => showQueue(batch, 1, { status, platform: queuePlatform })} />
            <SelectField label="Platform" value={queuePlatform} choices={queuePlatforms}
              onChange={(platform) => showQueue(batch, 1, { status: queueStatus, platform })} />
            {filteredQueue && <button type="button" className="secondary-button" onClick={() => showQueue(batch)}>Clear filters</button>}
          </fieldset>
          {queueError && <div className="queue-error">
            <p className="field-error" role="alert">{queueError}</p>
            <button type="button" className="secondary-button" onClick={() => reloadPosts()}>Try again</button>
          </div>}
          <div aria-busy={loadingQueue}>
          {loadingQueue ? <p className="muted" role="status">Loading posts…</p> : currentQueue &&
            (currentQueue.posts.length === 0 ? <p className="muted">{filteredQueue ? "No posts match these filters." : "Approved posts and platform status appear here."}</p> :
            <ul>{currentQueue.posts.map((post) => (
              <li className="publish-queue-row" key={post.id}>
                <div className="publish-queue-heading"><strong>{post.title}</strong><span className={`publish-status publish-${post.status}`}>{statuses[post.status]}</span></div>
                <small>{platforms[post.platform].name} · {post.account} · {new Date(post.scheduled_at).toLocaleString()}</small>
                <p>{post.message}</p>
                {["sending", "processing"].includes(post.status) && <progress value={post.progress} max={100} aria-label="Transfer progress" />}
                <div className="publish-buttons">
                  {!batch && post.batch_id && <button className="secondary-button" type="button" disabled={Boolean(busy)}
                    onClick={() => showQueue(post.batch_id)}>View batch</button>}
                  {post.job_id && <Link className="secondary-button" href={`/jobs/${post.job_id}`}>View clip</Link>}
                  {post.url && <a className="secondary-button" href={post.url} target="_blank" rel="noreferrer">Open post <ExternalLink size={14} aria-hidden="true" /></a>}
                  {post.can_apply_thumbnail && <button type="button" className="secondary-button" disabled={Boolean(busy)}
                    onClick={() => perform("thumbnail", async () => {
                      await submit(`publishing/posts/${post.id}/thumbnail/`, {});
                      await reloadPosts();
                      setMessage("Thumbnail update queued for the existing video.");
                    })}>{post.thumbnail_state === "applied" ? "Update thumbnail" : "Apply thumbnail"}</button>}
                  {post.platform === "tiktok" && post.status === "inbox" && post.caption &&
                    <button className="secondary-button" type="button" onClick={() => copy(post.caption)}>Copy caption</button>}
                  {post.job_id && ([["cancel", "Cancel"], ["retry", "Retry"], ["check", "Check status"]]).map(([action, label]) =>
                    ((action === "cancel" && post.status === "queued" && !post.started_at) ||
                      (action === "retry" && post.status === "failed") || (action === "check" && post.status === "inbox")) &&
                    <button type="button" className="secondary-button" key={action} disabled={Boolean(busy)}
                      onClick={() => perform(action, async () => { await submit(`publishing/posts/${post.id}/${action}/`, {}); await reloadPosts(); })}>{label}</button>
                  )}
                </div>
              </li>
            ))}</ul>)}
          </div>
          {pagination?.pages > 1 && <nav className="publish-queue-pagination" aria-label="Queue pages">
            <button type="button" className="secondary-button" disabled={Boolean(busy) || !currentQueue || pagination.page <= 1}
              onClick={() => goToQueuePage(pagination.page - 1)}><ChevronLeft size={17} aria-hidden="true" /> Previous</button>
            <span>Page {pagination.page} of {pagination.pages}</span>
            <button type="button" className="secondary-button" disabled={Boolean(busy) || !currentQueue || pagination.page >= pagination.pages}
              onClick={() => goToQueuePage(pagination.page + 1)}>Next <ChevronRight size={17} aria-hidden="true" /></button>
          </nav>}
        </section>
      )}
    </section>
  );
}
