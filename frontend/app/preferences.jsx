"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { api, submit } from "./api";

const initialPreferences = {
  selection_mode: "count", min_viral_score: 80,
  clip_count: 5, aspect_ratio: "9:16", quality: "720", language: "",
  framing: "auto", caption_style: "classic",
  min_clip_seconds: null, max_clip_seconds: null,
  theme: "light", show_scores: true,
};

const PreferencesContext = createContext(null);

export function PreferencesProvider({ children }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [request, setRequest] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    api("preferences/", { signal: abort.signal })
      .then((response) => setData(response))
      .catch((failure) => {
        if (failure.name !== "AbortError") setError(failure.message);
      });
    return () => abort.abort();
  }, [request]);

  const theme = data?.preferences.theme;
  useEffect(() => {
    if (!theme) return;
    const darkSystem = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      document.documentElement.dataset.theme = theme === "system"
        ? (darkSystem.matches ? "dark" : "light") : theme;
    };
    apply();
    try {
      localStorage.setItem("cutframe-theme", theme);
    } catch {
      // The server still saves the preference when browser storage is unavailable.
    }
    if (theme === "system") {
      darkSystem.addEventListener("change", apply);
      return () => darkSystem.removeEventListener("change", apply);
    }
  }, [theme]);

  function reload() {
    setError("");
    setRequest((current) => current + 1);
  }

  async function save(preferences) {
    setData(await submit("preferences/", preferences));
  }

  return (
    <PreferencesContext.Provider value={{
      preferences: data?.preferences ?? initialPreferences,
      defaults: data?.defaults ?? initialPreferences,
      loading: !data && !error,
      error,
      reload,
      save,
    }}>
      {children}
    </PreferencesContext.Provider>
  );
}

export function usePreferences() {
  return useContext(PreferencesContext);
}
