import { useState } from "react";
import { Wifi, Lock, RefreshCw, Loader2 } from "lucide-react";

function signalBars(signal) {
  if (signal >= 75) return "▂▄▆█";
  if (signal >= 50) return "▂▄▆ ";
  if (signal >= 25) return "▂▄  ";
  return "▂   ";
}

export default function NetworkPanel() {
  const [scanning, setScanning] = useState(false);
  const [networks, setNetworks] = useState([]);
  const [selected, setSelected] = useState(null);
  const [password, setPassword] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [message, setMessage] = useState("");
  const [messageOk, setMessageOk] = useState(true);

  const scan = async () => {
    setScanning(true);
    setMessage("");
    try {
      const res = await fetch("/api/network/wifi/scan", { method: "POST", credentials: "include" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Scan failed");
      const found = data.networks ?? [];
      setNetworks(found);
      setMessage(`Found ${found.length} network(s)`);
      setMessageOk(true);
    } catch (e) {
      setMessage(e.message);
      setMessageOk(false);
    } finally {
      setScanning(false);
    }
  };

  const connect = async () => {
    if (!selected) return;
    setConnecting(true);
    setMessage("");
    try {
      const res = await fetch("/api/network/wifi/connect", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ssid: selected.ssid, password }),
      });
      const data = await res.json();
      setMessage(data.message ?? (data.ok ? "Connected" : "Connection failed"));
      setMessageOk(!!data.ok);
      if (data.ok) setPassword("");
    } catch (e) {
      setMessage(e.message);
      setMessageOk(false);
    } finally {
      setConnecting(false);
    }
  };

  return (
    <div style={{ maxWidth: 520, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ fontSize: "1rem", fontWeight: 600, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 8 }}>
          <Wifi size={18} /> WiFi Networks
        </h2>
        <button onClick={scan} disabled={scanning} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {scanning ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
          {scanning ? "Scanning…" : "Scan"}
        </button>
      </div>

      {message && (
        <div style={{
          marginBottom: 12, fontSize: "0.85em",
          color: messageOk ? "var(--accent)" : "var(--danger)",
        }}>
          {message}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 320, overflowY: "auto" }}>
        {networks.length === 0 && !scanning && (
          <div style={{ color: "var(--text-secondary)", fontSize: "0.85em", padding: "12px 4px" }}>
            Tap Scan to discover nearby WiFi networks.
          </div>
        )}
        {networks.map((net) => {
          const isSel = selected?.ssid === net.ssid;
          return (
            <button
              key={net.ssid}
              onClick={() => { setSelected(net); setPassword(""); setMessage(""); }}
              style={{
                display: "flex", alignItems: "center", gap: 10, textAlign: "left",
                padding: "10px 14px", borderRadius: 10,
                border: isSel ? "1px solid var(--accent)" : "1px solid var(--glass-border)",
                background: isSel ? "var(--accent-glow)" : "var(--glass-bg)",
              }}
            >
              <span style={{ fontFamily: "monospace", opacity: 0.8 }}>{signalBars(net.signal)}</span>
              {net.security ? <Lock size={13} style={{ opacity: 0.7 }} /> : null}
              <span style={{ flex: 1 }}>{net.ssid}</span>
            </button>
          );
        })}
      </div>

      {selected && (
        <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 10 }}>
          {selected.security ? (
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={{ padding: "8px 12px", borderRadius: 8 }}
            />
          ) : null}
          <button className="primary" onClick={connect} disabled={connecting}>
            {connecting ? "Connecting…" : `Connect to ${selected.ssid}`}
          </button>
        </div>
      )}

      <style>{`
        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { 100% { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
