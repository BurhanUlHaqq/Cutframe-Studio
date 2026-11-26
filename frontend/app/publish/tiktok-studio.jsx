"use client";

import { useRef, useState } from "react";
import { Clipboard, Download, ExternalLink } from "lucide-react";
import { platforms } from "./platforms";

export default function TikTokStudio({ clip, caption, onCaption }) {
  const [feedback, setFeedback] = useState("");
  const field = useRef(null);

  async function copyCaption() {
    try {
      await navigator.clipboard.writeText(caption);
      setFeedback("Caption copied.");
    } catch {
      field.current?.focus();
      field.current?.select();
      setFeedback("Caption selected. Use your browser’s Copy command.");
    }
  }

  return (
    <section className="panel settings-panel tiktok-studio" aria-labelledby="tiktok-studio-title">
      <div className="panel-heading">
        <span className="heading-icon"><platforms.tiktok.Icon size={20} aria-hidden="true" /></span>
        <div><h2 id="tiktok-studio-title">Publish in TikTok Studio</h2>
          <p>Open TikTok Studio in a new tab and use your TikTok sign-in in this browser.</p></div>
      </div>
      <div className="tiktok-studio-details">
        <div className="publish-fields tiktok-caption">
          <label htmlFor="tiktok-caption">TikTok caption</label>
          <textarea id="tiktok-caption" ref={field} value={caption} rows={5} disabled={!clip}
            placeholder={clip ? undefined : "Select a clip to prepare its caption"}
            onChange={(event) => { setFeedback(""); onCaption(event.target.value); }} />
          <div className="tiktok-caption-tools">
            <button type="button" className="secondary-button" disabled={!clip || !caption.trim()} onClick={copyCaption}>
              <Clipboard size={16} aria-hidden="true" /> Copy caption
            </button>
            <p className="muted" role="status">{feedback}</p>
          </div>
        </div>
        <div className="tiktok-studio-upload">
          <h3>Upload your video</h3>
          <p className="muted">Download the MP4, then upload it and paste your caption in TikTok Studio. Choose the cover and visibility there before posting.</p>
          <div className="publish-buttons">
            {clip && <a className="secondary-button" href={`${clip.video_url}?download=1`}>
              <Download size={16} aria-hidden="true" /> Download MP4
            </a>}
            <a className="primary-button" href={platforms.tiktok.upload} target="_blank" rel="noopener noreferrer">
              <ExternalLink size={16} aria-hidden="true" /> Open TikTok Studio
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
