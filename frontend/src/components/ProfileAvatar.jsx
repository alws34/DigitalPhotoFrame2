import { useEffect, useState } from 'react';
import { User } from 'lucide-react';

/**
 * Circular profile picture. Falls back to a generic User icon when no picture is set.
 * size: CSS size string (e.g. "32px") — applied to both width and height.
 * refreshKey: bump to force a re-fetch (e.g. after uploading a new picture).
 */
export default function ProfileAvatar({ size = '32px', refreshKey = 0, style = {} }) {
  const [src, setSrc] = useState(null);

  useEffect(() => {
    // Cheap HEAD check – avoids flashing a broken-image icon
    fetch('/api/profile/picture', { method: 'HEAD' })
      .then((r) => { if (r.ok) setSrc(`/api/profile/picture?v=${Date.now()}`); else setSrc(null); })
      .catch(() => setSrc(null));
  }, [refreshKey]);

  const dim = { width: size, height: size, borderRadius: '50%', flexShrink: 0, ...style };

  if (src) {
    return (
      <img
        src={src}
        alt="Profile"
        style={{ ...dim, objectFit: 'cover', display: 'block' }}
        onError={() => setSrc(null)}
      />
    );
  }

  const px = parseInt(size, 10) || 32;
  return (
    <div style={{
      ...dim,
      background: 'rgba(255,255,255,0.08)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    }}>
      <User size={Math.round(px * 0.55)} color="var(--text-secondary)" />
    </div>
  );
}
