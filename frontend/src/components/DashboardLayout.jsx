import { Outlet, NavLink } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import { MonitorPlay, Aperture, Settings, LogOut, BookImage, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import ProfileAvatar from './ProfileAvatar';

const NAV_ITEMS = [
  { path: '/stream',   icon: <MonitorPlay size={20} />, label: 'Live Stream' },
  { path: '/gallery',  icon: <Aperture size={20} />,    label: 'Gallery'     },
  { path: '/albums',   icon: <BookImage size={20} />,   label: 'Albums'      },
  { path: '/settings', icon: <Settings size={20} />,    label: 'Settings'    },
];

export default function DashboardLayout() {
  const { user, logout } = useAuth();
  const { sidebarCollapsed, setSidebarCollapsed } = useTheme();

  const collapsed = sidebarCollapsed;

  return (
    <div className="app-container">
      <aside className={`sidebar ${collapsed ? 'collapsed' : 'expanded'} fade-in`}>

        {/* Header row */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'space-between',
          marginBottom: '1.5rem',
          gap: '0.5rem',
          width: '100%',
        }}>
          {!collapsed && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', overflow: 'hidden' }}>
              <div style={{ padding: '0.4rem', borderRadius: '8px', background: 'var(--accent)', color: 'white', flexShrink: 0 }}>
                <MonitorPlay size={18} />
              </div>
              <span style={{ fontWeight: 700, fontSize: '1rem', letterSpacing: '-0.3px', whiteSpace: 'nowrap' }}>PhotoFrame</span>
            </div>
          )}
          <button
            onClick={() => setSidebarCollapsed(!collapsed)}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            style={{
              background: 'transparent', border: 'none', padding: '6px',
              color: 'var(--text-secondary)', display: 'flex', alignItems: 'center',
              borderRadius: '6px', flexShrink: 0,
            }}
          >
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          </button>
        </div>

        {/* Nav */}
        <nav style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', flex: 1, width: '100%' }}>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              title={collapsed ? item.label : undefined}
              style={({ isActive }) => ({
                display: 'flex', alignItems: 'center',
                justifyContent: collapsed ? 'center' : 'flex-start',
                gap: '0.7rem',
                padding: collapsed ? '0.7rem' : '0.65rem 0.875rem',
                borderRadius: '10px',
                color: isActive ? '#fff' : 'var(--text-secondary)',
                backgroundColor: isActive ? 'var(--accent)' : 'transparent',
                boxShadow: isActive ? '0 2px 12px var(--accent-glow)' : 'none',
                transition: 'all var(--transition)',
                textDecoration: 'none',
                width: '100%',
              })}
            >
              {item.icon}
              {!collapsed && <span style={{ fontWeight: 500, fontSize: '0.9rem', whiteSpace: 'nowrap' }}>{item.label}</span>}
            </NavLink>
          ))}
        </nav>

        {/* Footer — profile picture + user info + sign out */}
        <div style={{
          marginTop: 'auto',
          paddingTop: '1rem',
          borderTop: '1px solid var(--glass-border)',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.75rem',
          width: '100%',
          alignItems: collapsed ? 'center' : 'stretch',
        }}>
          {!collapsed ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', color: 'var(--text-secondary)' }}>
              <ProfileAvatar size="36px" style={{ flexShrink: 0 }} />
              <div style={{ fontSize: '0.85rem', overflow: 'hidden' }}>
                <div style={{ color: 'white', fontWeight: 600, whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden' }}>{user?.username}</div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{user?.role}</div>
              </div>
            </div>
          ) : (
            <ProfileAvatar size="32px" />
          )}

          <button
            onClick={logout}
            title={collapsed ? 'Sign Out' : undefined}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              gap: '0.4rem', width: '100%', background: 'transparent',
              border: '1px solid var(--glass-border)', color: 'var(--text-secondary)',
              padding: collapsed ? '0.6rem' : '0.6rem 0.875rem', borderRadius: '10px',
            }}
          >
            <LogOut size={15} />
            {!collapsed && <span style={{ fontSize: '0.85rem' }}>Sign Out</span>}
          </button>
        </div>
      </aside>

      <main className="main-content fade-in" style={{ animationDelay: '0.05s' }}>
        <Outlet />
      </main>
    </div>
  );
}
