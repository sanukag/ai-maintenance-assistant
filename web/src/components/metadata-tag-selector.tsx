"use client";

import { KeyboardEvent, useId, useMemo, useState } from "react";

type MetadataTagSelectorProps = {
  label: string;
  values: string[];
  options: string[];
  onChange: (values: string[]) => void;
};

const MAX_VALUES = 20;

export function MetadataTagSelector({
  label,
  values,
  options,
  onChange,
}: MetadataTagSelectorProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const listboxId = useId();
  const inputId = useId();
  const selectedKeys = useMemo(
    () => new Set(values.map((value) => value.toLocaleLowerCase())),
    [values],
  );
  const suggestions = useMemo(() => {
    const search = query.trim().toLocaleLowerCase();
    return options.filter((option) => (
      !selectedKeys.has(option.toLocaleLowerCase())
      && (!search || option.toLocaleLowerCase().includes(search))
    ));
  }, [options, query, selectedKeys]);

  function addValue(rawValue: string) {
    const value = rawValue.trim().replace(/\s+/g, " ");
    if (!value || values.length >= MAX_VALUES) return;
    if (!selectedKeys.has(value.toLocaleLowerCase())) onChange([...values, value]);
    setQuery("");
    setOpen(true);
  }

  function removeValue(value: string) {
    onChange(values.filter((item) => item !== value));
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      addValue(query || suggestions[0] || "");
    } else if (event.key === "Backspace" && !query && values.length) {
      removeValue(values.at(-1) ?? "");
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="metadata-tag-selector">
      <label className="metadata-tag-label" htmlFor={inputId}>{label}</label>
      <div className={`metadata-tag-control ${open ? "metadata-tag-control-open" : ""}`}>
        <div className="metadata-tag-values">
          {values.map((value) => (
            <span className="metadata-tag" key={value}>
              {value}
              <button
                type="button"
                aria-label={`Remove ${value} from ${label.toLowerCase()}`}
                onClick={() => removeValue(value)}
              >
                ×
              </button>
            </span>
          ))}
          <input
            id={inputId}
            aria-label={`Add ${label.toLowerCase()}`}
            role="combobox"
            aria-autocomplete="list"
            aria-controls={listboxId}
            aria-expanded={open}
            value={query}
            maxLength={100}
            disabled={values.length >= MAX_VALUES}
            placeholder={values.length ? "Add another…" : "Type or choose…"}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => window.setTimeout(() => setOpen(false), 100)}
            onKeyDown={onKeyDown}
          />
        </div>
        {open && suggestions.length > 0 && (
          <div className="metadata-tag-menu" id={listboxId} role="listbox" aria-label={`${label} suggestions`}>
            {suggestions.map((option) => (
              <button
                type="button"
                role="option"
                aria-selected="false"
                key={option}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => addValue(option)}
              >
                <span>{option}</span>
                <small>Add</small>
              </button>
            ))}
          </div>
        )}
      </div>
      <small className="metadata-tag-hint">
        {values.length ? `${values.length} selected` : "Optional"} · Press Enter to add a new value
      </small>
    </div>
  );
}
