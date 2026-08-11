import { useState } from 'react'
import { useAuth } from '../../context/AuthContext'
import { getCalendarOAuthUrl } from '../../api/calendar'
import { API_BASE_URL } from '../../api/client'

// The OAuth callback page is rendered by the BACKEND (localhost:8000), not this frontend, so
// e.origin below is the backend's origin - compare against that, not window.location.origin.
const BACKEND_ORIGIN = new URL(API_BASE_URL).origin

export default function ConnectCalendarCard({ onConnected }) {
  const { token } = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const connect = async () => {
    setBusy(true); setError('')
    try {
      const { url } = await getCalendarOAuthUrl(token)
      const popup = window.open(url, 'google-calendar-oauth', 'width=520,height=640')

      // The backend's callback page postMessages back then closes itself.
      const onMessage = (e) => {
        if (e.origin !== BACKEND_ORIGIN || e.data?.type !== 'calendar_oauth') return
        window.removeEventListener('message', onMessage)
        setBusy(false)
        if (e.data.ok) onConnected?.()
        else setError('Could not connect Google Calendar.')
      }
      window.addEventListener('message', onMessage)

      // Fallback: the user closed the popup by hand, so no message ever arrives.
      const timer = setInterval(() => {
        if (popup?.closed) { clearInterval(timer); window.removeEventListener('message', onMessage); setBusy(false) }
      }, 500)
    } catch (err) {
      setError(err.detail?.message || err.detail || 'Could not open the Google authorization window.')
      setBusy(false)
    }
  }

  return (
    <section className="content-card text-center py-5">
      <i className="bi bi-calendar-plus display-4 text-muted d-block mb-3" />
      <h3>Connect your Google Calendar</h3>
      <p className="text-muted mb-4">
        Orbit needs access to your calendar to show and create events there. Your calendar isn't
        visible to anyone else in the app.
      </p>
      {error && <div className="auth-error mb-3">{error}</div>}
      <button className="btn btn-primary" onClick={connect} disabled={busy}>
        <i className="bi bi-google me-2" />{busy ? 'Waiting for Google...' : 'Connect Google Calendar'}
      </button>
    </section>
  )
}
