import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { useNavigate, Link } from 'react-router-dom';
import { Lock, Eye, EyeOff } from 'lucide-react';
import ProfileAvatar from '../components/ProfileAvatar';

export default function Login() {
  const [identity, setIdentity] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      setError('');
      await login({ username: identity, password });
      navigate('/');
    } catch (err) {
      setError(err.response?.data?.error || 'Login failed');
    }
  };

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: '100vh',
    }}>
      <div className="glass fade-in" style={{ padding: 40, width: 380, maxWidth: '100%' }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '1rem' }}>
            <ProfileAvatar size="72px" />
          </div>
          <h1 style={{ margin: 0, fontSize: '1.5rem' }}>Digital Photo Frame</h1>
          <p style={{ color: 'var(--text-muted)', margin: '0.5rem 0 0' }}>Sign in to manage your frame</p>
        </div>

        {error && (
          <div style={{
            background: 'var(--danger)',
            color: 'white',
            padding: '0.75rem',
            borderRadius: '8px',
            marginBottom: '1.5rem',
            fontSize: '0.9rem',
            textAlign: 'center',
          }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          <div>
            <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
              Email or Username
            </label>
            <input
              type="text"
              value={identity}
              onChange={(e) => setIdentity(e.target.value)}
              style={{ width: '100%' }}
              required
              autoFocus
            />
          </div>

          <div style={{ position: 'relative' }}>
            <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
              Password
            </label>
            <div style={{ position: 'relative' }}>
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                style={{ width: '100%', paddingRight: '2.5rem' }}
                required
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                style={{
                  position: 'absolute', right: '0.5rem', top: '50%',
                  transform: 'translateY(-50%)', background: 'none', border: 'none',
                  padding: '4px', color: 'var(--text-muted)', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', minWidth: 'auto', minHeight: 'auto',
                }}
              >
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
          </div>

          <button type="submit" className="primary" style={{ marginTop: '1rem', padding: '0.75rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem' }}>
            <Lock size={18} /> Sign In
          </button>
        </form>

        <div style={{ marginTop: '2rem', textAlign: 'center', fontSize: '0.9rem', color: 'var(--text-muted)' }}>
          <div style={{ marginBottom: '1rem' }}>
            <Link to="/reset-password" style={{ color: 'var(--text-muted)', textDecoration: 'none' }}>Forgot password?</Link>
          </div>
          Don't have an account?{' '}
          <Link to="/signup" style={{ color: 'var(--primary)', fontWeight: 500, textDecoration: 'none' }}>Sign up</Link>
        </div>
      </div>
    </div>
  );
}
