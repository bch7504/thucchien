import { apiFetch } from './client'

export const listTasks = (token) => apiFetch('/tasks', { token })

export const createTask = (token, { title, due_at, priority, conversation_id, source }) =>
  apiFetch('/tasks', { method: 'POST', token, body: { title, due_at, priority, conversation_id, source } })

export const updateTaskStatus = (token, taskId, status) =>
  apiFetch(`/tasks/${taskId}/status`, { method: 'PATCH', token, body: { status } })

export const deleteTask = (token, taskId) =>
  apiFetch(`/tasks/${taskId}`, { method: 'DELETE', token })
