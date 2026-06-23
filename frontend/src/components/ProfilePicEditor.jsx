import { useCallback, useEffect, useRef, useState } from 'react';
import { Upload, ZoomIn, ZoomOut, Trash2, Check, X } from 'lucide-react';
import ProfileAvatar from './ProfileAvatar';

const CANVAS_SIZE = 260; // px – displayed crop circle diameter

export default function ProfilePicEditor({ onSaved }) {
  const [mode, setMode] = useState('idle'); // 'idle' | 'editing' | 'saving'
  const [imgSrc, setImgSrc] = useState(null);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [scale, setScale] = useState(1);
  const [avatarKey, setAvatarKey] = useState(0);
  const [hasPicture, setHasPicture] = useState(false);
  const [error, setError] = useState('');

  const imgRef = useRef(null);
  const dragging = useRef(false);
  const dragStart = useRef({ mx: 0, my: 0, ox: 0, oy: 0 });

  useEffect(() => {
    fetch('/api/profile/picture', { method: 'HEAD' })
      .then((r) => setHasPicture(r.ok))
      .catch(() => setHasPicture(false));
  }, [avatarKey]);

  const onFileChange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    setImgSrc(url);
    setOffset({ x: 0, y: 0 });
    setScale(1);
    setMode('editing');
    setError('');
  };

  const onPointerDown = useCallback((e) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    dragging.current = true;
    dragStart.current = { mx: e.clientX, my: e.clientY, ox: offset.x, oy: offset.y };
  }, [offset]);

  const onPointerMove = useCallback((e) => {
    if (!dragging.current) return;
    setOffset({
      x: dragStart.current.ox + (e.clientX - dragStart.current.mx),
      y: dragStart.current.oy + (e.clientY - dragStart.current.my),
    });
  }, []);

  const onPointerUp = useCallback(() => { dragging.current = false; }, []);

  const onWheel = useCallback((e) => {
    e.preventDefault();
    setScale((s) => Math.max(0.5, Math.min(4, s - e.deltaY * 0.002)));
  }, []);

  const bakeAndUpload = useCallback(async () => {
    if (!imgRef.current) return;
    setMode('saving');
    setError('');
    try {
      const canvas = document.createElement('canvas');
      canvas.width = CANVAS_SIZE;
      canvas.height = CANVAS_SIZE;
      const ctx = canvas.getContext('2d');

      // Clip to circle
      ctx.beginPath();
      ctx.arc(CANVAS_SIZE / 2, CANVAS_SIZE / 2, CANVAS_SIZE / 2, 0, Math.PI * 2);
      ctx.clip();

      const img = imgRef.current;
      const baseScale = CANVAS_SIZE / Math.min(img.naturalWidth, img.naturalHeight);
      const finalScale = baseScale * scale;
      const dw = img.naturalWidth * finalScale;
      const dh = img.naturalHeight * finalScale;
      const dx = (CANVAS_SIZE - dw) / 2 + offset.x;
      const dy = (CANVAS_SIZE - dh) / 2 + offset.y;

      ctx.drawImage(img, dx, dy, dw, dh);

      const blob = await new Promise((res) => canvas.toBlob(res, 'image/png'));
      const fd = new FormData();
      fd.append('file', blob, 'profile.png');

      const res = await fetch('/api/profile/picture', {
        method: 'POST', credentials: 'include', body: fd,
      });
      if (!res.ok) throw new Error((await res.json()).error ?? 'Upload failed');

      setAvatarKey((k) => k + 1);
      setHasPicture(true);
      setMode('idle');
      setImgSrc(null);
      onSaved?.();
    } catch (e) {
      setError(e.message);
      setMode('editing');
    }
  }, [imgRef, scale, offset, onSaved]);

  const handleDelete = async () => {
    await fetch('/api/profile/picture', { method: 'DELETE', credentials: 'include' });
    setHasPicture(false);
    setAvatarKey((k) => k + 1);
    onSaved?.();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1.25rem' }}>

      {/* Current picture or crop editor */}
      {mode === 'idle' ? (
        <div style={{ position: 'relative', display: 'inline-block' }}>
          <ProfileAvatar size="120px" refreshKey={avatarKey} />
          {hasPicture && (
            <button
              onClick={handleDelete}
              title="Remove picture"
              style={{
                position: 'absolute', top: 0, right: -8, background: 'var(--danger)',
                border: 'none', borderRadius: '50%', width: 28, height: 28,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: 'pointer', padding: 0,
              }}
            >
              <Trash2 size={14} color="white" />
            </button>
          )}
        </div>
      ) : (
        /* Drag-to-crop editor */
        <div style={{ position: 'relative', userSelect: 'none' }}>
          <div
            style={{
              width: CANVAS_SIZE, height: CANVAS_SIZE, borderRadius: '50%',
              overflow: 'hidden', border: '3px solid var(--accent)',
              cursor: 'grab', background: '#111',
            }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onWheel={onWheel}
          >
            {imgSrc && (
              <img
                ref={imgRef}
                src={imgSrc}
                alt="crop"
                draggable={false}
                style={{
                  position: 'absolute',
                  left: '50%', top: '50%',
                  transform: `translate(calc(-50% + ${offset.x}px), calc(-50% + ${offset.y}px)) scale(${scale})`,
                  maxWidth: 'none',
                  pointerEvents: 'none',
                }}
              />
            )}
          </div>
          <p style={{ textAlign: 'center', fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: 6 }}>
            Drag to reposition · scroll to zoom
          </p>
        </div>
      )}

      {/* Zoom controls (editing only) */}
      {mode === 'editing' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button onClick={() => setScale((s) => Math.max(0.5, s - 0.1))} title="Zoom out">
            <ZoomOut size={16} />
          </button>
          <input
            type="range" min={0.5} max={4} step={0.05} value={scale}
            onChange={(e) => setScale(Number(e.target.value))}
            style={{ width: 120 }}
          />
          <button onClick={() => setScale((s) => Math.min(4, s + 0.1))} title="Zoom in">
            <ZoomIn size={16} />
          </button>
        </div>
      )}

      {error && <p style={{ color: 'var(--danger)', fontSize: '0.85rem' }}>{error}</p>}

      {/* Action buttons */}
      <div style={{ display: 'flex', gap: 10 }}>
        {mode === 'editing' ? (
          <>
            <button className="primary" onClick={bakeAndUpload} disabled={mode === 'saving'}
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Check size={16} /> {mode === 'saving' ? 'Saving…' : 'Save'}
            </button>
            <button onClick={() => { setMode('idle'); setImgSrc(null); }}
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <X size={16} /> Cancel
            </button>
          </>
        ) : (
          <label style={{
            display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
            padding: '0.5rem 1rem', borderRadius: 8,
            background: 'var(--accent-glow)', color: 'var(--accent-hover)',
            border: '1px solid var(--accent)', fontSize: '0.9rem', fontWeight: 500,
          }}>
            <Upload size={16} />
            {hasPicture ? 'Change picture' : 'Upload picture'}
            <input type="file" accept="image/*" style={{ display: 'none' }} onChange={onFileChange} />
          </label>
        )}
      </div>
    </div>
  );
}
