import { useEffect, useState } from 'react'
import { useAuth } from '../../context/AuthContext'
import { listUsers, createConversation } from '../../api/chat'
import Avatar from '../common/Avatar'
import { getInitials, getColor } from '../../utils/avatar'

export default function NewConversationModal({ open, onClose, onCreated }) {
  const { token } = useAuth()
  const [users, setUsers] = useState([])
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState([])
  const [groupName, setGroupName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    listUsers(token, search).then(setUsers).catch(() => setUsers([]))
  }, [open, search, token])

  useEffect(() => { if (!open) { setSelected([]); setGroupName(''); setSearch(''); setError('') } }, [open])

  if (!open) return null

  const toggle = (id) => setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])

  const submit = async (e) => {
    e.preventDefault()
    if (!selected.length) return
    if (selected.length > 1 && !groupName.trim()) { setError('Group name is required'); return }
    setSubmitting(true); setError('')
    try {
      const conv = await createConversation(token, {
        type: selected.length > 1 ? 'group' : 'direct',
        participant_ids: selected,
        name: selected.length > 1 ? groupName.trim() : undefined,
      })
      onCreated(conv)
      onClose()
    } catch (err) { setError(err.detail || 'Could not start conversation') }
    finally { setSubmitting(false) }
  }

  return (
    <div className="modal show d-block" tabIndex="-1" style={{ background: 'rgba(20,30,50,.32)' }} onClick={onClose}>
      <div className="modal-dialog modal-dialog-centered" onClick={e => e.stopPropagation()}>
        <div className="modal-content">
          <div className="modal-header"><h5 className="modal-title">New conversation</h5><button className="btn-close" onClick={onClose} /></div>
          <form onSubmit={submit}>
            <div className="modal-body">
              {error && <div className="auth-error">{error}</div>}
              <input className="form-control mb-3" placeholder="Search people..." value={search} onChange={e => setSearch(e.target.value)} />
              {selected.length > 1 && (
                <input className="form-control mb-3" placeholder="Group name" value={groupName} onChange={e => setGroupName(e.target.value)} />
              )}
              <div className="d-flex flex-column gap-2" style={{ maxHeight: 260, overflowY: 'auto' }}>
                {users.map(u => (
                  <label key={u.id} className="d-flex align-items-center gap-2" style={{ cursor: 'pointer' }}>
                    <input type="checkbox" checked={selected.includes(u.id)} onChange={() => toggle(u.id)} />
                    <Avatar initials={getInitials(u.display_name)} color={getColor(u.id)} size={32} />
                    <span>{u.display_name}<small className="d-block text-muted">{u.email}</small></span>
                  </label>
                ))}
                {!users.length && <p className="text-muted small mb-0">No users found.</p>}
              </div>
            </div>
            <div className="modal-footer">
              <button type="button" className="btn btn-light" onClick={onClose}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={submitting || !selected.length}>
                {submitting ? 'Starting...' : 'Start conversation'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
