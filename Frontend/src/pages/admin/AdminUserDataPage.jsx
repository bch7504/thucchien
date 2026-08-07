import { useEffect, useState } from 'react'
import PageHeader from '../../components/common/PageHeader'
import AdminTaskTable from '../../components/admin/AdminTaskTable'
import AdminReminderTable from '../../components/admin/AdminReminderTable'
import AdminMemoryTable from '../../components/admin/AdminMemoryTable'
import { useAuth } from '../../context/AuthContext'
import {
  listUsers,
  listTasks, deleteTask,
  listReminders, deleteReminder,
  listMemories, deleteMemory,
} from '../../api/admin'

const TABS = [
  { key: 'tasks', label: 'Tasks' },
  { key: 'reminders', label: 'Reminders' },
  { key: 'memories', label: 'Memories' },
]

const listFor = { tasks: listTasks, reminders: listReminders, memories: listMemories }
const deleteFor = { tasks: deleteTask, reminders: deleteReminder, memories: deleteMemory }
const confirmLabel = { tasks: 'task', reminders: 'reminder', memories: 'memory' }

export default function AdminUserDataPage() {
  const { token } = useAuth()
  const [tab, setTab] = useState('tasks')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [users, setUsers] = useState([])
  const [ownerFilter, setOwnerFilter] = useState('')

  useEffect(() => { listUsers(token).then(setUsers) }, [token])

  const refresh = () => {
    setLoading(true)
    listFor[tab](token, ownerFilter || undefined).then(setItems).finally(() => setLoading(false))
  }

  useEffect(() => { refresh() }, [token, tab, ownerFilter])

  const onDelete = async (item) => {
    if (!window.confirm(`Delete this ${confirmLabel[tab]}? This cannot be undone.`)) return
    await deleteFor[tab](token, item.id)
    setItems(list => list.filter(x => x.id !== item.id))
  }

  return (
    <div className="page-container">
      <PageHeader eyebrow="Admin" title="User data" description="View and manage any user's tasks, reminders, and memories." />
      <section className="content-card">
        <div className="card-toolbar">
          <div className="btn-group">
            {TABS.map(t => (
              <button key={t.key} className={`btn btn-sm ${tab === t.key ? 'btn-primary' : 'btn-light'}`} onClick={() => setTab(t.key)}>{t.label}</button>
            ))}
          </div>
          <div className="toolbar-actions">
            <select className="form-select form-select-sm" value={ownerFilter} onChange={e => setOwnerFilter(e.target.value)}>
              <option value="">All users</option>
              {users.map(u => <option key={u.id} value={u.id}>{u.display_name} ({u.email})</option>)}
            </select>
          </div>
        </div>
        {loading ? <p className="text-muted small p-3 mb-0">Loading...</p> : (
          <>
            {tab === 'tasks' && <AdminTaskTable tasks={items} onDelete={onDelete} />}
            {tab === 'reminders' && <AdminReminderTable reminders={items} onDelete={onDelete} />}
            {tab === 'memories' && <AdminMemoryTable memories={items} onDelete={onDelete} />}
          </>
        )}
      </section>
    </div>
  )
}
