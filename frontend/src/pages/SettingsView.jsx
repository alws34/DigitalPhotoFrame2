import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import SettingsSection from "../components/settings/SettingsSection";
import EffectsPanel from "../components/effects/EffectsPanel";
import ProfilePicEditor from "../components/ProfilePicEditor";
import NetworkPanel from "../components/settings/NetworkPanel";

const HIDDEN_KEYS = new Set(["about"]);
const MERGED_KEYS = new Set(["backend_configs", "autoupdate", "playback", "screen", "admin_ui", "stats", "effects", "stream"]);

const TAB_GROUPS = {
  system: ["backend_configs", "autoupdate", "screen", "admin_ui", "stream", "playback"],
  ui: ["stats", "effects"],
};

const TAB_LABELS = {
  open_meteo: "Weather",
  ui: "Frame UI",
};

const TAB_ORDER = ["system", "ui", "albums", "mqtt", "open_meteo", "network", "profile"];

const SECTION_LABELS = {
  backend_configs: "Backend Config",
  autoupdate:      "Auto Update",
  playback:        "Playback",
  screen:          "Screen",
  admin_ui:        "Admin UI",
  stats:           "Stats",
  effects:         "Effects",
  stream:          "Stream",
};

function tabLabel(key) {
  if (key === "profile") return "Profile";
  if (key === "network") return "Network";
  return TAB_LABELS[key] ?? key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function setNestedValue(obj, path, value) {
  const [head, ...rest] = path.split(".");
  if (rest.length === 0) return { ...obj, [head]: value };
  return { ...obj, [head]: setNestedValue(obj[head] ?? {}, rest.join("."), value) };
}

function collectChangedPaths(original, updated, prefix = "") {
  const paths = new Set();
  for (const key of Object.keys(updated)) {
    const path = prefix ? `${prefix}.${key}` : key;
    const orig = original?.[key];
    const upd = updated[key];
    if (upd !== null && typeof upd === "object" && !Array.isArray(upd)) {
      const sub = collectChangedPaths(orig, upd, path);
      sub.forEach((p) => paths.add(p));
    } else if (orig !== upd) {
      paths.add(path);
    }
  }
  return paths;
}

function gatherRestartPaths(schema, prefix = "") {
  const paths = new Set();
  for (const [key, val] of Object.entries(schema)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (val && typeof val === "object") {
      if ("type" in val) {
        if (val.restart_required) paths.add(path);
      } else {
        const sub = gatherRestartPaths(val, path);
        sub.forEach((p) => paths.add(p));
      }
    }
  }
  return paths;
}

export default function SettingsView() {
  const [settings, setSettings] = useState(null);
  const [schema, setSchema] = useState({});
  const [activeTab, setActiveTab] = useState(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");
  const [showRestartModal, setShowRestartModal] = useState(false);
  const originalRef = useRef(null);
  const pendingRef = useRef(null);
  const [albums, setAlbums] = useState([]);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await fetch("/api/settings", { credentials: "include" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setSettings(data);
      originalRef.current = data;
    } catch (e) {
      setStatus(`Error: ${e.message}`);
    }
  }, []);

  useEffect(() => { fetchSettings(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    fetch("/api/albums", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setAlbums(Array.isArray(d) ? d : []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch("/api/settings/schema")
      .then((r) => r.json())
      .then(setSchema)
      .catch(() => {});
  }, []);

  useEffect(() => {
    const es = new EventSource("/api/settings/events", { withCredentials: true });
    es.onmessage = (e) => {
      if (e.data === "settings_updated" && !pendingRef.current) fetchSettings();
    };
    return () => es.close();
  }, [fetchSettings]);

  // Build ordered tab list, injecting "profile" and "network" as synthetic tabs
  const tabs = useMemo(() => {
    if (!settings) return [];
    const keys = new Set(Object.keys(settings).filter((k) => !HIDDEN_KEYS.has(k) && !MERGED_KEYS.has(k)));
    const pinned = TAB_ORDER.filter((k) => keys.has(k) || k === "profile" || k === "network");
    const pinnedSet = new Set(pinned);
    const rest = [...keys].filter((k) => !pinnedSet.has(k)).sort();
    return [...pinned, ...rest];
  }, [settings]);

  useEffect(() => {
    if (settings && !activeTab && tabs.length > 0) {
      setActiveTab(tabs[0]);
    }
  }, [settings, activeTab, tabs]);

  const handleChange = useCallback((path, value) => {
    setSettings((prev) => {
      const next = setNestedValue(prev, path, value);
      pendingRef.current = next;
      return next;
    });
    setStatus("Unsaved changes");
  }, []);

  const handleSave = async () => {
    const snapshot = pendingRef.current;
    if (!snapshot) return;
    setSaving(true);
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(snapshot),
      });
      if (!res.ok) throw new Error(await res.text());

      const changed = collectChangedPaths(originalRef.current, snapshot);
      const restartPaths = gatherRestartPaths(schema);
      const needsRestart = [...changed].some((p) => restartPaths.has(p));

      originalRef.current = snapshot;
      if (pendingRef.current === snapshot) pendingRef.current = null;
      setStatus("Saved");
      setTimeout(() => setStatus(""), 2000);
      if (needsRestart) setShowRestartModal(true);
    } catch (e) {
      setStatus(`Save failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleRestart = async () => {
    setShowRestartModal(false);
    try {
      await fetch("/api/maintenance/restart", { method: "POST", credentials: "include" });
      setStatus("Restarting…");
    } catch (e) {
      setStatus(`Restart failed: ${e.message}`);
    }
  };

  if (!settings) {
    return <div style={{ padding: 40, color: "var(--text-secondary)" }}>Loading settings…</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: 24, gap: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ fontSize: "1.4em", fontWeight: 600 }}>Settings</h1>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {status && (
            <span style={{
              color: status.startsWith("Error") || status.startsWith("Save failed")
                ? "var(--danger)" : "var(--accent)",
              fontSize: "0.85em",
            }}>
              {status}
            </span>
          )}
          {/* Don't show Save on profile/network tabs — nothing there is persisted settings */}
          {activeTab !== "profile" && activeTab !== "network" && (
            <button className="primary" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          )}
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {tabs.map((key) => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            style={{
              padding: "6px 16px", borderRadius: 20,
              border: activeTab === key ? "1px solid var(--accent)" : "1px solid var(--glass-border)",
              background: activeTab === key ? "var(--accent-glow)" : "var(--glass-bg)",
              color: activeTab === key ? "var(--accent-hover)" : "var(--text-secondary)",
              fontWeight: activeTab === key ? 600 : 400,
            }}
          >
            {tabLabel(key)}
          </button>
        ))}
      </div>

      <div className="glass" style={{ flex: 1, overflowY: "auto", padding: 20 }}>
        {/* Profile tab — synthetic, no settings key */}
        {activeTab === "profile" && (
          <div style={{ maxWidth: 480, margin: "0 auto" }}>
            <h2 style={{ fontSize: "1rem", fontWeight: 600, marginBottom: 20, color: "var(--text-secondary)" }}>Profile Picture</h2>
            <ProfilePicEditor />
          </div>
        )}

        {/* Network tab — synthetic, no settings key */}
        {activeTab === "network" && <NetworkPanel />}

        {activeTab && activeTab !== "profile" && activeTab !== "network" && (
          <>
            {/* Primary section */}
            {settings[activeTab] && typeof settings[activeTab] === "object" && !Array.isArray(settings[activeTab]) && (
              <SettingsSection
                data={settings[activeTab]}
                pathPrefix={activeTab}
                schema={schema[activeTab] ?? {}}
                onChange={handleChange}
                extras={{ albums }}
              />
            )}
            {/* Merged sections — effects gets EffectsPanel, others get SettingsSection */}
            {(TAB_GROUPS[activeTab] ?? []).map((key) =>
              settings[key] && typeof settings[key] === "object" && !Array.isArray(settings[key]) ? (
                <div key={key} style={{ marginTop: 24 }}>
                  <div style={{
                    fontSize: "0.72em", fontWeight: 700, letterSpacing: "0.1em",
                    color: "var(--text-secondary)", textTransform: "uppercase",
                    paddingBottom: 8, borderBottom: "1px solid var(--glass-border)",
                    marginBottom: 12,
                  }}>
                    {SECTION_LABELS[key] ?? key}
                  </div>
                  {key === "effects" ? (
                    <EffectsPanel
                      effects={settings.effects ?? {}}
                      onChange={handleChange}
                      originalEffects={originalRef.current?.effects}
                    />
                  ) : (
                    <SettingsSection
                      data={settings[key]}
                      pathPrefix={key}
                      schema={schema[key] ?? {}}
                      onChange={handleChange}
                      extras={{ albums }}
                    />
                  )}
                </div>
              ) : null
            )}
          </>
        )}
      </div>

      {showRestartModal && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
          display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
        }}>
          <div className="glass" style={{ padding: 32, maxWidth: 420, borderRadius: 16, textAlign: "center" }}>
            <h2 style={{ marginBottom: 12 }}>Restart Required</h2>
            <p style={{ color: "var(--text-secondary)", marginBottom: 24 }}>
              Some changes require a restart to take effect.
            </p>
            <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
              <button className="primary" onClick={handleRestart}>Restart Now</button>
              <button onClick={() => setShowRestartModal(false)}>Later</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
