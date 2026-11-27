"use client";

import { useEffect, useId, useRef } from "react";
import { ChevronDown } from "lucide-react";

export function formatSourceTimestamp(milliseconds) {
  if (milliseconds == null) return "";
  const seconds = Math.floor(milliseconds / 1000);
  const totalMinutes = Math.floor(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = hours ? String(totalMinutes % 60).padStart(2, "0") : String(totalMinutes);
  const fraction = milliseconds % 1000;
  return `${hours ? `${hours}:` : ""}${minutes}:${String(seconds % 60).padStart(2, "0")}${fraction
    ? `.${String(fraction).padStart(3, "0").replace(/0+$/, "")}` : ""}`;
}

export default function SourceRangeOptions({ value, onChange, highlights, errors = {}, download = false }) {
  const id = useId();
  const details = useRef(null);
  const hasError = Boolean(errors.source_start || errors.source_end);
  const sourceStart = value.source_start || "";
  const sourceEnd = value.source_end || "";

  useEffect(() => {
    if (hasError && details.current) {
      details.current.open = true;
      details.current.querySelector('[aria-invalid="true"]')?.focus();
    }
  }, [hasError]);

  return (
    <details className="advanced source-range-options" ref={details}>
      <summary>Advanced options <ChevronDown size={15} aria-hidden="true" />
        {(sourceStart || sourceEnd) && <span className="language-selection">Source range set</span>}
      </summary>
      <fieldset className="control-group">
        <legend>Source timestamps</legend>
        <div className="options">
          {[["source_start", "Start time", sourceStart, "1:10:00"], ["source_end", "End time", sourceEnd, "1:10:30"]]
            .map(([field, label, timestamp, placeholder]) => <div key={field}>
              <label htmlFor={`${id}-${field}`}>{label}</label>
              <div className="input-wrap">
                <input id={`${id}-${field}`} type="text" autoComplete="off" spellCheck={false} maxLength={24}
                  value={timestamp} placeholder={placeholder} aria-invalid={Boolean(errors[field])}
                  aria-describedby={`${id}-hint${errors[field] ? ` ${id}-${field}-error` : ""}`}
                  onChange={(event) => onChange({ [field]: event.target.value })} />
              </div>
              {errors[field] && <p id={`${id}-${field}-error`} className="field-error" role="alert">{errors[field][0].message}</p>}
            </div>)}
        </div>
        <small id={`${id}-hint`} className="selection-hint">
          {download ? "Save this time range from each individual link." : highlights
            ? "Find highlights only between these times." : "Cut one clip between these times."}
          {" Use H:MM:SS, M:SS, or seconds. Leave both blank to use the whole source."}
        </small>
      </fieldset>
    </details>
  );
}
