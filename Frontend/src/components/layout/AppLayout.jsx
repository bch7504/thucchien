import { useCallback, useRef, useState } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import Sidebar from './Sidebar'
import TopNavbar from './TopNavbar'
import ReminderToast from './ReminderToast'
import TaskSuggestedToast from './TaskSuggestedToast'
import BudgetAlertToast from './BudgetAlertToast'
import { useAuth } from '../../context/AuthContext'
import { useChatSocket } from '../../api/useWebSocket'
import { getNotificationPermission, isNotificationSupported, notifyTaskSuggested } from '../../utils/browserNotifications'

export default function AppLayout() {
  const [open, setOpen] = useState(false)
  const { token, user } = useAuth()
  const navigate = useNavigate()
  const handlersRef = useRef(new Set())
  const [toastReminder, setToastReminder] = useState(null)
  const [toastTask, setToastTask] = useState(null)
  const [toastBudget, setToastBudget] = useState(null)

  const subscribe = useCallback((handler) => {
    handlersRef.current.add(handler)
    return () => handlersRef.current.delete(handler)
  }, [])

  // Native OS notification for a proactively-suggested task, on top of (not instead of) the
  // in-page toast below - only when the user opted in (Profile > Notifications > "AI suggestion
  // alerts") and this tab is backgrounded/minimized (visible tab already gets the toast, firing
  // both would just duplicate the alert). Never re-requests permission here - that only happens
  // from the toggle's own click in ProfilePage.jsx, a real user gesture; a background WS message
  // is not one, and browsers ignore/auto-deny permission requests outside a gesture anyway.
  const maybeNotifyTaskSuggested = task => {
    if (user?.preferences?.ai_suggestion_alerts !== true) return
    if (document.visibilityState === 'visible') return
    if (!isNotificationSupported() || getNotificationPermission() !== 'granted') return
    notifyTaskSuggested(task, { onClick: () => navigate('/tasks') })
  }

  const { sendJson } = useChatSocket(token, (data) => {
    handlersRef.current.forEach(handler => handler(data))
    if (data.type === 'reminder_fired') setToastReminder(data.reminder)
    if (data.type === 'task_suggested') { setToastTask(data.task); maybeNotifyTaskSuggested(data.task) }
    if (data.type === 'usage_budget_alert') setToastBudget(data)
  })

  return (
    <div className="app-shell">
      <Sidebar open={open} onClose={() => setOpen(false)} />
      <div className="app-column"><TopNavbar onMenu={() => setOpen(true)} /><main className="app-main"><Outlet context={{ sendJson, subscribe }} /></main></div>
      {toastReminder && <ReminderToast reminder={toastReminder} onClose={() => setToastReminder(null)} />}
      {toastTask && <TaskSuggestedToast task={toastTask} onClose={() => setToastTask(null)} />}
      {toastBudget && <BudgetAlertToast alert={toastBudget} onClose={() => setToastBudget(null)} />}
    </div>
  )
}
