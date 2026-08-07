import { useEffect, useState } from 'react'
import PageHeader from '../../components/common/PageHeader'
import UserTable from '../../components/admin/UserTable'
import { useAuth } from '../../context/AuthContext'
import { listUsers, updateUserRole, updateUserStatus } from '../../api/admin'

export default function AdminUsersPage() {
  const { token, user } = useAuth()
  const [users, setUsers] = useState([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)

  const refresh = () => {
    setLoading(true)
    listUsers(token, search).then(setUsers).finally(() => setLoading(false))
  }

  useEffect(() => { refresh() }, [token, search])

  const toggleRole = async (u) => {
    const updated = await updateUserRole(token, u.id, u.role === 'admin' ? 'user' : 'admin')
    setUsers(list => list.map(x => x.id === updated.id ? updated : x))
  }

  const toggleStatus = async (u) => {
    const updated = await updateUserStatus(token, u.id, !u.is_active)
    setUsers(list => list.map(x => x.id === updated.id ? updated : x))
  }

  return (
    <div className="page-container">
      <PageHeader eyebrow="Admin" title="User management" description="Promote/demote accounts and lock or unlock access." />
      <section className="content-card">
        <div className="card-toolbar">
          <div><h3>All users</h3><span>{users.length} accounts</span></div>
          <div className="toolbar-actions">
            <div className="mini-search"><i className="bi bi-search" /><input placeholder="Search by name or email" value={search} onChange={e => setSearch(e.target.value)} /></div>
          </div>
        </div>
        {loading ? <p className="text-muted small p-3 mb-0">Loading...</p> : (
          <UserTable users={users} currentUserId={user?.id} onToggleRole={toggleRole} onToggleStatus={toggleStatus} />
        )}
      </section>
    </div>
  )
}
