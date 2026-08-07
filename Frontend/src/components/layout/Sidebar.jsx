import { NavLink } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'

const nav = [
  ['assistant', 'bi-stars', 'AI Assistant'], ['chat', 'bi-chat-dots', 'Chats'], ['tasks', 'bi-check2-square', 'Tasks'],
  ['tasks/inbox', 'bi-inbox', 'Inbox'],
  ['calendar', 'bi-calendar4-week', 'Calendar'], ['reminders', 'bi-bell', 'Reminders'],
  ['memory', 'bi-stars', 'Memory'], ['profile', 'bi-person', 'Profile'],
]

const getInitials = (name) => (name || '?').trim().split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase()

const adminNav = [
  ['admin', 'bi-speedometer2', 'Dashboard'], ['admin/users', 'bi-people', 'Users'], ['admin/conversations', 'bi-chat-square-text', 'Conversations'],
  ['admin/user-data', 'bi-database', 'User data'],
]

export default function Sidebar({ open, onClose }) {
  const { user, isAdmin } = useAuth()
  return (
    <>
      <div className={`sidebar-backdrop ${open ? 'show' : ''}`} onClick={onClose} />
      <aside className={`app-sidebar ${open ? 'open' : ''}`}>
        <div className="brand"><span className="brand-mark"><i className="bi bi-command" /></span><span>Orbit</span></div>
        <nav className="sidebar-nav">
          <div className="nav-caption">Workspace</div>
          {nav.map(([path, icon, label]) => (
            // `end` matters here: without it, `/tasks` would also read as "active" while on
            // `/tasks/inbox` (NavLink prefix-matches by default), highlighting both at once.
            <NavLink key={path} to={`/${path}`} end onClick={onClose} className={({ isActive }) => `side-link ${label === 'AI Assistant' ? 'assistant-link' : ''} ${isActive ? 'active' : ''}`}>
              <i className={`bi ${icon}`} /><span>{label}</span>{label === 'AI Assistant' && <span className="new-pill">New</span>}
            </NavLink>
          ))}
          {isAdmin && <>
            <div className="nav-caption">Admin</div>
            {adminNav.map(([path, icon, label]) => (
              <NavLink key={path} to={`/${path}`} end onClick={onClose} className={({ isActive }) => `side-link ${isActive ? 'active' : ''}`}>
                <i className={`bi ${icon}`} /><span>{label}</span>
              </NavLink>
            ))}
          </>}
        </nav>
        <div className="sidebar-bottom">
          <div className="ai-usage"><div className="d-flex align-items-center gap-2 mb-2"><i className="bi bi-stars" /><strong>AI credits</strong><span>72%</span></div><div className="progress"><div className="progress-bar" style={{width:'72%'}} /></div><small>Resets in 12 days</small></div>
          <NavLink to="/profile" className="user-mini"><span className="avatar-photo">{getInitials(user?.display_name)}</span><span><strong>{user?.display_name || 'Loading...'}</strong><small>{user?.email}</small></span><i className="bi bi-three-dots ms-auto" /></NavLink>
        </div>
      </aside>
    </>
  )
}
