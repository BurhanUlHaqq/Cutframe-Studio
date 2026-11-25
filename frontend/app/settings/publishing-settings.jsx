"use client";

import { useEffect, useId, useState } from "react";
import Link from "next/link";
import { Check, Clipboard, Link2, Save } from "lucide-react";
import { api, submit } from "../api";
import { platforms } from "../publish/platforms";

function SetupInput({ label, value, onChange, type = "text", placeholder, maxLength = 255 }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <div className="input-wrap">
        <input id={id} type={type} value={value} onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder} maxLength={maxLength} spellCheck={false}
          autoComplete={type === "password" ? "new-password" : "off"} />
      </div>
    </div>
  );
}

export default function PublishingSettings() {
  const [data, setData] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [notice, setNotice] = useState({});
  const [busy, setBusy] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api("publishing/settings/", { signal: controller.signal })
      .then(setData)
      .catch((error) => { if (!controller.signal.aborted) setNotice({ error: error.message }); });
    return () => controller.abort();
  }, [attempt]);

  function change(section, key, value) {
    setDrafts((current) => ({ ...current, [section]: { ...current[section], [key]: value } }));
    setNotice({});
  }

  async function save(event, section, fields) {
    event.preventDefault();
    if (busy) return;
    setBusy(section);
    setNotice({});
    try {
      const updated = await submit(`publishing/settings/${section}/`, fields);
      setData(updated);
      setDrafts((current) => ({ ...current, [section]: {} }));
      setNotice({ section, message: "Settings saved." });
    } catch (error) {
      setNotice({ section, error: Object.values(error.fields || {})[0]?.[0]?.message || error.message });
    } finally {
      setBusy("");
    }
  }

  function actions(section) {
    return (
      <div className="settings-actions">
        <div className="settings-feedback">
          {notice.section === section && (notice.error
            ? <p className="field-error" role="alert">{notice.error}</p>
            : <p className="settings-saved" role="status"><Check size={16} aria-hidden="true" /> {notice.message}</p>)}
        </div>
        <button className="primary-button" disabled={Boolean(busy) || !Object.keys(drafts[section] || {}).length}>
          <Save size={16} aria-hidden="true" /> {busy === section ? "Saving…" : "Save settings"}
        </button>
      </div>
    );
  }

  async function copy(section, value) {
    try {
      await navigator.clipboard.writeText(value);
      setNotice({ section, message: "Callback copied." });
    } catch {
      setNotice({ section, error: "Select and copy the callback address manually." });
    }
  }

  const studio = drafts.network?.studio_origin ?? data?.studio_origin;
  const callback = drafts.network?.callback_origin ?? data?.callback_origin;

  return (
    <div className="publishing-settings">
      <section className="panel settings-panel" aria-labelledby="publishing-settings-title">
        <div className="panel-heading">
          <span className="heading-icon"><Link2 size={21} aria-hidden="true" /></span>
          <div><h2 id="publishing-settings-title" tabIndex={-1}>Publishing</h2>
            <p>Save developer app credentials and OAuth callbacks for this Cutframe installation.</p></div>
        </div>
        {!data ? (
          notice.error ? <><p className="field-error" role="alert">{notice.error}</p>
            <button className="secondary-button settings-retry" type="button" onClick={() => {
              setNotice({}); setAttempt((n) => n + 1);
            }}>Try again</button></> : <p className="muted" role="status">Loading publishing settings…</p>
        ) : (
          <form onSubmit={(event) => save(event, "network", { studio_origin: studio, callback_origin: callback })}>
            <fieldset className="control-group" disabled={Boolean(busy)}>
              <legend className="sr-only">Callback network</legend>
              <div className="ai-fields">
                <SetupInput label="Local studio address" value={studio} type="url"
                  onChange={(value) => change("network", "studio_origin", value)} />
                <SetupInput label="Public HTTPS callback origin" value={callback} type="url"
                  placeholder="https://cutframe-auth.example.com"
                  onChange={(value) => change("network", "callback_origin", value)} />
              </div>
              <p className="ai-help">YouTube uses the local address. TikTok and Instagram use a stable HTTPS hostname.
                Route only their callback paths to Cutframe and open the studio at its local address.</p>
              <a className="secondary-button" href="https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/"
                target="_blank" rel="noreferrer">Cloudflare Tunnel setup</a>
            </fieldset>
            {actions("network")}
          </form>
        )}
      </section>
      {data?.platforms.map((entry) => {
        const section = entry.platform;
        const platform = platforms[section];
        const meta = section === "instagram";
        const fields = { client_id: entry.client_id, client_secret: "", remove_secret: false,
          ...(meta ? { instagram_login_config: data.instagram_login_config, meta_api_version: data.meta_api_version } : {}),
          ...drafts[section] };
        return (
          <section className="panel settings-panel" key={section} aria-labelledby={`publisher-${section}`}>
            <div className="panel-heading">
              <span className="heading-icon"><platform.Icon size={21} aria-hidden="true" /></span>
              <div><h3 id={`publisher-${section}`}>{entry.name}</h3>
                <p>{entry.client_id && entry.has_secret ? "Developer credentials saved" : "Developer credentials required"}</p></div>
            </div>
            <form onSubmit={(event) => save(event, section, fields)}>
              <fieldset className="control-group" disabled={Boolean(busy)}>
                <legend className="sr-only">{entry.name} developer app</legend>
                <div className="ai-fields">
                  <SetupInput label={platform.idLabel} value={fields.client_id}
                    onChange={(value) => change(section, "client_id", value)} />
                  <SetupInput label={platform.secretLabel} value={fields.client_secret} type="password" maxLength={1000}
                    placeholder={entry.has_secret && fields.client_id === entry.client_id ? "Saved credential" : "Enter a secret"}
                    onChange={(value) => change(section, "client_secret", value)} />
                  {meta && <>
                    <SetupInput label="Login configuration ID (optional)" value={fields.instagram_login_config} maxLength={64}
                      onChange={(value) => change(section, "instagram_login_config", value)} />
                    <SetupInput label="Meta Graph API version" value={fields.meta_api_version} maxLength={12}
                      onChange={(value) => change(section, "meta_api_version", value)} />
                  </>}
                </div>
                {entry.has_secret && <label className="preference-check">
                  <input type="checkbox" checked={fields.remove_secret}
                    onChange={(event) => change(section, "remove_secret", event.target.checked)} />
                  <span>Remove stored secret when saved</span>
                </label>}
                <p className="ai-help">Changing the app ID requires its matching secret. Open Publish Studio to connect the account.</p>
                {entry.redirect_uri ? <div className="publishing-callback">
                  <span>Register this redirect URI in the developer dashboard</span>
                  <code className="publish-callback">{entry.redirect_uri}</code>
                  <button className="secondary-button" type="button" onClick={() => copy(section, entry.redirect_uri)}>
                    <Clipboard size={16} aria-hidden="true" /> Copy callback
                  </button>
                </div> : <p className="ai-help">Save a public HTTPS callback origin to see this platform’s redirect URI.</p>}
              </fieldset>
              {actions(section)}
            </form>
          </section>
        );
      })}
      {data && <Link className="secondary-button" href="/publish">Open Publish Studio</Link>}
    </div>
  );
}
