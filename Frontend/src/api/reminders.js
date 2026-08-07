import { apiFetch } from './client'

export const listReminders = (token) => apiFetch('/reminders', { token })

export const createReminder = (token, { title, due_at_iso, lead_minutes, message }) =>
  apiFetch('/reminders', { method: 'POST', token, body: { title, due_at_iso, lead_minutes, message } })

export const cancelReminder = (token, reminderId) =>
  apiFetch(`/reminders/${reminderId}`, { method: 'DELETE', token })
