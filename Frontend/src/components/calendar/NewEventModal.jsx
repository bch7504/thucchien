import { useState } from 'react'
import { useAuth } from '../../context/AuthContext'
import { createCalendarEvent } from '../../api/calendar'

export default function NewEventModal({ open, onClose, onCreated }) {
  const { token } = useAuth()
  const [summary, setSummary] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [description, setDescription] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  if (!open) return null

  const submit = async (e) => {
    e.preventDefault()
    if (!summary.trim() || !start || !end) return
    setSubmitting(true); setError('')
    try {
      const event = await createCalendarEvent(token, {
        summary: summary.trim(),
        start_iso: `${start}:00`,
        end_iso: `${end}:00`,
        description: description.trim() || undefined,
      })
      onCreated(event)
      onClose()
      setSummary(''); setStart(''); setEnd(''); setDescription('')
    } catch (err) { setError(err.detail || 'Could not create event') }
    finally { setSubmitting(false) }
  }

  return (
    <div className="modal show d-block" tabIndex="-1" style={{ background: 'rgba(20,30,50,.32)' }} onClick={onClose}>
      <div className="modal-dialog modal-dialog-centered" onClick={e => e.stopPropagation()}>
        <div className="modal-content">
          <div className="modal-header"><h5 className="modal-title">New event</h5><button className="btn-close" onClick={onClose} /></div>
          <form onSubmit={submit}>
            <div className="modal-body d-flex flex-column gap-3">
              {error && <div className="auth-error">{error}</div>}
              <input className="form-control" placeholder="Event title" value={summary} onChange={e => setSummary(e.target.value)} required />
              <div className="row g-2">
                <div className="col"><label className="form-label small">Start</label><input type="datetime-local" className="form-control" value={start} onChange={e => setStart(e.target.value)} required /></div>
                <div className="col"><label className="form-label small">End</label><input type="datetime-local" className="form-control" value={end} onChange={e => setEnd(e.target.value)} required /></div>
              </div>
              <textarea className="form-control" placeholder="Description (optional)" value={description} onChange={e => setDescription(e.target.value)} rows={3} />
            </div>
            <div className="modal-footer">
              <button type="button" className="btn btn-light" onClick={onClose}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={submitting}>{submitting ? 'Creating...' : 'Create event'}</button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
