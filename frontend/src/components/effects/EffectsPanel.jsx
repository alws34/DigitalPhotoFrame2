import { useCallback, useEffect, useRef, useState } from 'react';
import { RotateCcw, RefreshCw } from 'lucide-react';

const PRESET_META = {
  milk_glass:   { label: 'Milk Glass',    desc: 'Soft white frosted' },
  tinted_glass: { label: 'Tinted Glass',  desc: 'Blue-tinted cool'   },
  frosted_dark: { label: 'Frosted Dark',  desc: 'Dark moody blur'    },
  clear:        { label: 'Clear',         desc: 'Minimal dim'        },
  custom:       { label: 'Custom',        desc: 'Manual controls'    },
};

const PRESET_CSS = {
  milk_glass:   { blur: 20, opacity: 0.3, tintColor: '#ffffff', tintOpacity: 0.3 },
  tinted_glass: { blur: 10, opacity: 0.5, tintColor: '#4488ff', tintOpacity: 0.2 },
  frosted_dark: { blur: 24, opacity: 0.8, tintColor: '#000000', tintOpacity: 0.4 },
  clear:        { blur: 3,  opacity: 0.15, tintColor: '#ffffff', tintOpacity: 0.0 },
};

function HexInput({ label, value, onChange }) {
  const [raw, setRaw] = useState(value);
  const [prevValue, setPrevValue] = useState(value);
  if (value !== prevValue) {
    setPrevValue(value);
    setRaw(value);
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{
        width: 32, height: 32, borderRadius: 6, border: '1px solid var(--glass-border)',
        background: value, flexShrink: 0, display: 'inline-block',
      }} />
      <div>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: 2 }}>{label}</div>
        <input
          type="text"
          value={raw}
          maxLength={7}
          onChange={(e) => {
            setRaw(e.target.value);
            if (/^#[0-9a-fA-F]{6}$/.test(e.target.value)) onChange(e.target.value);
          }}
          onBlur={() => { if (!/^#[0-9a-fA-F]{6}$/.test(raw)) setRaw(value); }}
          style={{ width: 110, fontFamily: 'monospace' }}
        />
      </div>
      <input
        type="color"
        value={value}
        onChange={(e) => { setRaw(e.target.value); onChange(e.target.value); }}
        style={{ width: 36, height: 32, padding: 2, border: '1px solid var(--glass-border)', borderRadius: 6, cursor: 'pointer', background: 'transparent' }}
      />
    </div>
  );
}

function Slider({ label, value, min, max, step, onChange }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr 52px', alignItems: 'center', gap: 10 }}>
      <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{label}</span>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value, 10))} />
      <input type="number" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value, 10))}
        style={{ width: '100%', textAlign: 'right' }} />
    </div>
  );
}

export default function EffectsPanel({ effects, onChange, originalEffects }) {
  const [snapshot, setSnapshot] = useState(null);
  const [loadingSnap, setLoadingSnap] = useState(true);
  const hasFetchedRef = useRef(false);

  const loadSnapshot = useCallback(() => {
    fetch('/api/stream/snapshot', { credentials: 'include' })
      .then((r) => r.ok ? r.blob() : null)
      .then((blob) => { if (blob) setSnapshot(URL.createObjectURL(blob)); })
      .catch(() => {})
      .finally(() => setLoadingSnap(false));
  }, []);

  const fetchSnapshot = useCallback(() => {
    setLoadingSnap(true);
    loadSnapshot();
  }, [loadSnapshot]);

  useEffect(() => {
    if (!hasFetchedRef.current) { hasFetchedRef.current = true; loadSnapshot(); }
  }, [loadSnapshot]);

  const set = (key, val) => onChange(`effects.${key}`, val);

  const bgType = effects.background_type ?? 'blur';
  const preset = effects.preset ?? 'custom';

  // Build CSS preview params
  const previewParams = preset !== 'custom'
    ? (PRESET_CSS[preset] ?? PRESET_CSS.clear)
    : {
        blur: Math.round((effects.background_blur_radius ?? 61) * 0.35),
        opacity: effects.background_opacity ?? 0.4,
        tintColor: effects.tint_color ?? '#000000',
        tintOpacity: effects.tint_opacity ?? 0.0,
      };

  // ----- Preview -----
  const renderPreview = () => {
    const s = {
      width: '100%', paddingTop: '42%', position: 'relative', borderRadius: 12,
      overflow: 'hidden', background: '#111', marginBottom: 20,
    };

    let bg;
    if (bgType === 'color') {
      bg = <div style={{ position: 'absolute', inset: 0, background: effects.background_color ?? '#000' }} />;
    } else if (bgType === 'none') {
      bg = <div style={{ position: 'absolute', inset: 0, background: '#000' }} />;
    } else {
      bg = (
        <>
          {snapshot && (
            <img src={snapshot} alt="" style={{
              position: 'absolute', inset: 0, width: '100%', height: '100%',
              objectFit: 'cover',
              filter: `blur(${previewParams.blur}px)`,
              transform: 'scale(1.08)',
              opacity: previewParams.opacity,
            }} />
          )}
          {previewParams.tintOpacity > 0 && (
            <div style={{
              position: 'absolute', inset: 0,
              background: previewParams.tintColor,
              opacity: previewParams.tintOpacity,
            }} />
          )}
          {!snapshot && (
            <div style={{
              position: 'absolute', inset: 0, display: 'flex',
              alignItems: 'center', justifyContent: 'center',
              color: 'var(--text-muted)', fontSize: '0.85rem',
            }}>
              {loadingSnap ? 'Loading preview…' : 'No preview — click Refresh'}
            </div>
          )}
        </>
      );
    }

    // Fake photo card overlay
    const card = (
      <div style={{
        position: 'absolute', left: '50%', top: '50%',
        transform: 'translate(-50%, -50%)',
        width: '30%', paddingTop: '22%',
        background: 'rgba(255,255,255,0.15)',
        borderRadius: 8, border: '1px solid rgba(255,255,255,0.2)',
        boxShadow: effects.shadow_enabled
          ? `0 0 ${effects.shadow_blur_radius ?? 71}px rgba(0,0,0,${effects.shadow_opacity ?? 0.85})`
          : 'none',
      }} />
    );

    return (
      <div style={s}>
        {bg}
        {card}
        <button
          onClick={fetchSnapshot}
          disabled={loadingSnap}
          title="Refresh preview"
          style={{
            position: 'absolute', top: 8, right: 8,
            background: 'rgba(0,0,0,0.55)', border: '1px solid rgba(255,255,255,0.2)',
            borderRadius: 6, padding: '4px 8px', color: 'white',
            display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.75rem',
          }}
        >
          <RefreshCw size={12} /> {loadingSnap ? 'Loading…' : 'Refresh'}
        </button>
      </div>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {renderPreview()}

      {/* Revert */}
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          onClick={() => {
            if (!originalEffects) return;
            Object.entries(originalEffects).forEach(([k, v]) => set(k, v));
          }}
          style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.85rem' }}
        >
          <RotateCcw size={14} /> Revert to saved
        </button>
      </div>

      {/* Background type */}
      <div>
        <div style={{ fontSize: '0.72em', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: 10 }}>
          Background Type
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {['blur', 'color', 'none'].map((t) => (
            <button
              key={t}
              onClick={() => set('background_type', t)}
              style={{
                flex: 1, padding: '0.5rem',
                borderRadius: 8,
                border: bgType === t ? '2px solid var(--accent)' : '1px solid var(--glass-border)',
                background: bgType === t ? 'var(--accent-glow)' : 'var(--glass-bg)',
                color: bgType === t ? 'var(--accent-hover)' : 'var(--text-secondary)',
                fontWeight: bgType === t ? 600 : 400,
                fontSize: '0.85rem',
              }}
            >
              {t === 'blur' ? 'Blurred Photo' : t === 'color' ? 'Solid Color' : 'None (Black)'}
            </button>
          ))}
        </div>
      </div>

      {/* Solid color picker */}
      {bgType === 'color' && (
        <div>
          <div style={{ fontSize: '0.72em', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: 10 }}>
            Background Color
          </div>
          <HexInput
            label="Hex color"
            value={effects.background_color ?? '#000000'}
            onChange={(v) => set('background_color', v)}
          />
          <button
            onClick={() => set('background_color', '#000000')}
            style={{ marginTop: 8, fontSize: '0.8rem', color: 'var(--text-muted)' }}
          >
            Reset to black
          </button>
        </div>
      )}

      {/* Presets (blur mode only) */}
      {bgType === 'blur' && (
        <div>
          <div style={{ fontSize: '0.72em', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: 10 }}>
            Preset
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: 8 }}>
            {Object.entries(PRESET_META).map(([key, meta]) => (
              <button
                key={key}
                onClick={() => set('preset', key)}
                style={{
                  padding: '0.6rem 0.5rem', borderRadius: 10, textAlign: 'center',
                  border: preset === key ? '2px solid var(--accent)' : '1px solid var(--glass-border)',
                  background: preset === key ? 'var(--accent-glow)' : 'var(--glass-bg)',
                  color: preset === key ? 'var(--accent-hover)' : 'var(--text-secondary)',
                  fontWeight: preset === key ? 600 : 400,
                  cursor: 'pointer',
                }}
              >
                <div style={{ fontSize: '0.85rem', marginBottom: 2 }}>{meta.label}</div>
                <div style={{ fontSize: '0.72rem', opacity: 0.7 }}>{meta.desc}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Manual controls — only shown in blur mode + custom preset */}
      {bgType === 'blur' && preset === 'custom' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: '0.72em', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: 4 }}>
            Custom Tune
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: '0.85rem' }}>
            <input type="checkbox" checked={effects.background_blur_enabled ?? true}
              onChange={(e) => set('background_blur_enabled', e.target.checked)} />
            Enable blur
          </label>
          {(effects.background_blur_enabled ?? true) && (
            <Slider label="Blur radius" value={effects.background_blur_radius ?? 61}
              min={0} max={200} step={2} onChange={(v) => set('background_blur_radius', v)} />
          )}
          <Slider label="Dimming opacity" value={effects.background_opacity ?? 0.4}
            min={0} max={1} step={0.05} onChange={(v) => set('background_opacity', v)} />
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: '0.72em', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: 8 }}>
              Tint
            </div>
            <HexInput label="Tint color" value={effects.tint_color ?? '#ffffff'}
              onChange={(v) => set('tint_color', v)} />
            <div style={{ marginTop: 10 }}>
              <Slider label="Tint opacity" value={effects.tint_opacity ?? 0.0}
                min={0} max={1} step={0.05} onChange={(v) => set('tint_opacity', v)} />
            </div>
          </div>
        </div>
      )}

      {/* Shadow — always shown */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ fontSize: '0.72em', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: 4 }}>
          Shadow
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: '0.85rem' }}>
          <input type="checkbox" checked={effects.shadow_enabled ?? true}
            onChange={(e) => set('shadow_enabled', e.target.checked)} />
          Enable shadow
        </label>
        {(effects.shadow_enabled ?? true) && (
          <>
            <Slider label="Shadow blur" value={effects.shadow_blur_radius ?? 71}
              min={0} max={200} step={2} onChange={(v) => set('shadow_blur_radius', v)} />
            <Slider label="Shadow opacity" value={effects.shadow_opacity ?? 0.85}
              min={0} max={1} step={0.05} onChange={(v) => set('shadow_opacity', v)} />
          </>
        )}
      </div>

    </div>
  );
}
