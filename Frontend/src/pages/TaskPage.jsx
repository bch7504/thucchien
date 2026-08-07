import { useEffect, useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import PageHeader from '../components/common/PageHeader'
import StatCard from '../components/common/StatCard'
import TaskTable, { formatDue } from '../components/task/TaskTable'
import { useAuth } from '../context/AuthContext'
import { listTasks, updateTaskStatus, deleteTask } from '../api/tasks'

const sourceLabel = { manual: 'Manual', proactive: 'AI suggestion' }

export default function TaskPage() {
  const { token } = useAuth()
  const { subscribe } = useOutletContext()
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')

  const refresh = () => {
    setLoading(true)
    listTasks(token).then(setTasks).finally(() => setLoading(false))
  }

  useEffect(() => { refresh() }, [token])

  const upsertTask = (task) => setTasks(prev => [...prev.filter(t => t.id !== task.id), task])
  const removeTask = (taskId) => setTasks(prev => prev.filter(t => t.id !== taskId))

  // Realtime: proactive suggestions land here the moment Orbit finds them, and any change made
  // from another tab/device (or the agent chat) shows up without a manual refresh. Harmless if
  // it duplicates an update this tab already applied optimistically below - upsert is idempotent.
  useEffect(() => subscribe((data) => {
    if (data.type === 'task_suggested' || data.type === 'task_created' || data.type === 'task_updated') upsertTask(data.task)
    if (data.type === 'task_deleted') removeTask(data.task_id)
  }), [subscribe])

  const suggestions = tasks.filter(t => t.status === 'suggested')
  const mainTasks = tasks.filter(t => t.status !== 'suggested' && t.status !== 'dismissed')
  const shownTasks = mainTasks.filter(t => t.title.toLowerCase().includes(query.toLowerCase()))
  const completed = mainTasks.filter(t => t.status === 'completed').length
  const overdue = mainTasks.filter(t => t.status === 'pending' && t.due_at && new Date(t.due_at) < new Date()).length
  const pending = mainTasks.length - completed - overdue

  const accept = (task) => updateTaskStatus(token, task.id, 'pending').then(upsertTask)
  const dismiss = (task) => updateTaskStatus(token, task.id, 'dismissed').then(upsertTask)
  const complete = (task) => updateTaskStatus(token, task.id, 'completed').then(upsertTask)
  const remove = (task) => deleteTask(token, task.id).then(() => removeTask(task.id))

  return <div className="page-container">
    <PageHeader eyebrow="Workspace" title="My Tasks" description="Stay on top of work, including action items found by Orbit." action={<div className="d-flex gap-2"><Link to="/tasks/inbox" className="btn btn-light rounded-3"><i className="bi bi-inbox me-2"/>Priority inbox</Link><button className="btn btn-primary rounded-3"><i className="bi bi-plus-lg me-2"/>Add task</button></div>}/>
    <div className="stats-grid"><StatCard label="Total tasks" value={mainTasks.length} icon="bi-list-task"/><StatCard label="Completed" value={completed} icon="bi-check2-circle" color="success"/><StatCard label="Pending" value={pending} icon="bi-hourglass-split" color="warning"/><StatCard label="Overdue" value={overdue} icon="bi-exclamation-circle" color="danger" note={overdue ? 'Needs attention' : undefined}/></div>
    <section className="content-card"><div className="card-toolbar"><div><h3>All tasks</h3><span>{shownTasks.length} of {mainTasks.length} tasks across your conversations</span></div><div className="toolbar-actions"><div className="mini-search"><i className="bi bi-search"/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search tasks"/></div></div></div>{loading ? <p className="text-muted small p-3 mb-0">Loading...</p> : <TaskTable tasks={shownTasks} onComplete={complete} onDelete={remove}/>}</section>
    <section className="suggested-section"><div className="section-heading"><div><span className="ai-label"><i className="bi bi-stars"/> AI suggestions</span><h3>Tasks you may have missed</h3><p>Orbit found these action items in your conversations.</p></div></div><div className="suggestion-grid">{suggestions.map(s=><div className="suggestion-card" key={s.id}><div className="suggestion-check"><i className="bi bi-stars"/></div><div className="flex-grow-1"><h4>{s.title}</h4><div className="suggestion-meta"><span><i className="bi bi-chat-left-text"/>{sourceLabel[s.source] || s.source}</span><span><i className="bi bi-calendar3"/>{formatDue(s.due_at)}</span></div></div><div className="suggestion-actions"><button className="btn btn-sm btn-primary" onClick={() => accept(s)}>Accept</button><button className="btn btn-sm btn-light" onClick={() => dismiss(s)}>Dismiss</button></div></div>)}
      {!loading && !suggestions.length && <p className="text-muted small mb-0">No new suggestions right now — try "Extract tasks" in a conversation's AI panel.</p>}
    </div></section>
  </div>
}
