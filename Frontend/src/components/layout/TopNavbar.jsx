import { useNavigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'

const getInitials = (name) => (name || '?').trim().split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase()

export default function TopNavbar({ onMenu }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const onLogout = () => { logout(); navigate('/login') }
  return (
    <header className="top-navbar">
      <button className="icon-btn mobile-menu" onClick={onMenu} aria-label="Open menu"><i className="bi bi-list" /></button>
      <div className="global-search"><i className="bi bi-search" /><input aria-label="Search" placeholder="Search anything..."/><kbd>⌘ K</kbd></div>
      <div className="nav-actions">
        <button className="icon-btn"><i className="bi bi-question-circle" /></button>
        <button className="icon-btn notification-btn"><i className="bi bi-bell" /><span /></button>
        <button className="nav-avatar">{getInitials(user?.display_name)}</button>
        <button className="icon-btn" onClick={onLogout} aria-label="Log out" title="Log out"><i className="bi bi-box-arrow-right" /></button>
      </div>
    </header>
  )
}
