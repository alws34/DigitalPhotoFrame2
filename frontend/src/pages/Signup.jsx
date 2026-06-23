import { useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { useNavigate, Link } from 'react-router-dom';
import { UserPlus, Eye, EyeOff, Camera } from 'lucide-react';

const CANVAS_SIZE = 180;

export default function Signup() {
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(false);

  // Profile picture state
  const [picSrc, setPicSrc] = useState(null);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [scale, setScale] = useState(1);
  const imgRef = useRef(null);
  const dragging = useRef(false);
  const dragStart = useRef({ mx: 0, my: 0, ox: 0, oy: 0 });

  const { signup } = useAuth();
  const navigate = useNavigate();

  const onFileChange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setPicSrc(URL.createObjectURL(file));
    setOffset({ x: 0, y: 0 });
    setScale(1);
  };

  const onPointerDown = (e) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    dragging.current = true;
    dragStart.current = { mx: e.clientX, my: e.clientY, ox: offset.x, oy: offset.y };
  };
  const onPointerMove = (e) => {
    if (!dragging.current) return;
    setOffset({ x: dragStart.current.ox + (e.clientX - dragStart.current.mx), y: dragStart.current.oy + (e.clientY - dragStart.current.my) });
  };
  const onPointerUp = () => { dragging.current = false; };
  const onWheel = (e) => { e.preventDefault(); setScale((s) => Math.max(0.5, Math.min(4, s - e.deltaY * 0.002))); };

  const bakePicture = () => new Promise((resolve) => {
    if (!picSrc || !imgRef.current) return resolve(null);
    const canvas = document.createElement('canvas');
    canvas.width = CANVAS_SIZE;
    canvas.height = CANVAS_SIZE;
    const ctx = canvas.getContext('2d');
    ctx.beginPath();
    ctx.arc(CANVAS_SIZE / 2, CANVAS_SIZE / 2, CANVAS_SIZE / 2, 0, Math.PI * 2);
    ctx.clip();
    const img = imgRef.current;
    const baseScale = CANVAS_SIZE / Math.min(img.naturalWidth, img.naturalHeight);
    const fs = baseScale * scale;
    ctx.drawImage(img, (CANVAS_SIZE - img.naturalWidth * fs) / 2 + offset.x, (CANVAS_SIZE - img.naturalHeight * fs) / 2 + offset.y, img.naturalWidth * fs, img.naturalHeight * fs);
    canvas.toBlob((blob) => resolve(blob), 'image/png');
  });

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      setError(''); setSuccess(''); setLoading(true);
      await signup({ email, username, password });

      // Upload profile picture if one was chosen
      const blob = await bakePicture();
      if (blob) {
        const fd = new FormData();
        fd.append('file', blob, 'profile.png');
        // Note: user is now logged in after signup redirect, so we attempt upload
        await fetch('/api/profile/picture', { method: 'POST', credentials: 'include', body: fd }).catch(() => {});
      }

      setSuccess('Account created! Redirecting…');
      setTimeout(() => navigate('/login'), 2000);
    } catch (err) {
      setError(err.response?.data?.error || 'Failed to create account.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      minHeight: '100vh', background: 'linear-gradient(135deg, #121212 0%, #1e1e1e 100%)', padding: '2rem',
    }}>
      <div className="glass-panel fade-in" style={{ padding: '2.5rem', width: '100%', maxWidth: '460px' }}>
        <div style={{ textAlign: 'center', marginBottom: '1.5rem' }}>
          <h1 style={{ margin: 0, fontSize: '1.5rem' }}>Create Account</h1>
          <p style={{ color: 'var(--text-muted)', margin: '0.5rem 0 0' }}>Sign up to manage your photo frame</p>
        </div>

        {/* Profile picture picker */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: '1.5rem', gap: 10 }}>
          {picSrc ? (
            <>
              <div
                style={{ width: CANVAS_SIZE, height: CANVAS_SIZE, borderRadius: '50%', overflow: 'hidden', border: '2px solid var(--accent)', cursor: 'grab', position: 'relative', background: '#111' }}
                onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onWheel={onWheel}
              >
                <img
                  ref={imgRef} src={picSrc} alt="crop" draggable={false}
                  style={{ position: 'absolute', left: '50%', top: '50%', transform: `translate(calc(-50% + ${offset.x}px), calc(-50% + ${offset.y}px)) scale(${scale})`, maxWidth: 'none', pointerEvents: 'none' }}
                />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input type="range" min={0.5} max={4} step={0.05} value={scale} onChange={(e) => setScale(Number(e.target.value))} style={{ width: 120 }} />
                <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Zoom</span>
              </div>
              <button onClick={() => setPicSrc(null)} style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Remove</button>
            </>
          ) : (
            <label style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
              cursor: 'pointer', padding: '1rem 1.5rem', borderRadius: 10,
              border: '1px dashed var(--glass-border)', color: 'var(--text-muted)', fontSize: '0.85rem',
            }}>
              <Camera size={28} color="var(--text-muted)" />
              Add profile picture <span style={{ fontSize: '0.75rem' }}>(optional)</span>
              <input type="file" accept="image/*" style={{ display: 'none' }} onChange={onFileChange} />
            </label>
          )}
        </div>

        {error && <div style={{ background: 'var(--danger)', color: 'white', padding: '0.75rem', borderRadius: '8px', marginBottom: '1.5rem', fontSize: '0.9rem', textAlign: 'center' }}>{error}</div>}
        {success && <div style={{ background: 'rgba(100, 108, 255, 0.2)', color: 'var(--primary)', border: '1px solid var(--primary)', padding: '0.75rem', borderRadius: '8px', marginBottom: '1.5rem', fontSize: '0.9rem', textAlign: 'center' }}>{success}</div>}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          <div>
            <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-muted)', fontSize: '0.9rem' }}>Email Address</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} style={{ width: '100%' }} required autoFocus />
          </div>
          <div>
            <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-muted)', fontSize: '0.9rem' }}>Username</label>
            <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} style={{ width: '100%' }} required minLength={3} pattern="[a-zA-Z0-9_]+" title="Only letters, numbers, and underscores allowed" />
          </div>
          <div>
            <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-muted)', fontSize: '0.9rem' }}>Password</label>
            <div style={{ position: 'relative' }}>
              <input type={showPassword ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)} style={{ width: '100%', paddingRight: '2.5rem' }} required minLength={8} />
              <button type="button" onClick={() => setShowPassword(!showPassword)} style={{ position: 'absolute', right: '0.5rem', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', padding: '4px', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex', alignItems: 'center', minWidth: 'auto', minHeight: 'auto' }}>
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>Must be at least 8 characters</div>
          </div>

          <button type="submit" className="primary" disabled={loading || !!success} style={{ marginTop: '1rem', padding: '0.75rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem' }}>
            <UserPlus size={18} /> {loading ? 'Creating…' : 'Sign Up'}
          </button>
        </form>

        <div style={{ marginTop: '2rem', textAlign: 'center', fontSize: '0.9rem', color: 'var(--text-muted)' }}>
          Already have an account?{' '}
          <Link to="/login" style={{ color: 'var(--primary)', fontWeight: 500, textDecoration: 'none' }}>Sign in instead</Link>
        </div>
      </div>
    </div>
  );
}
