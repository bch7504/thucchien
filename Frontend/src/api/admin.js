import { apiFetch } from './client'

export const getStats = (token) => apiFetch('/admin/stats', { token })

export const listUsers = (token, q) =>
  apiFetch(`/admin/users${q ? `?q=${encodeURIComponent(q)}` : ''}`, { token })

export const updateUserRole = (token, userId, role) =>
  apiFetch(`/admin/users/${userId}/role`, { method: 'PATCH', token, body: { role } })

export const updateUserStatus = (token, userId, is_active) =>
  apiFetch(`/admin/users/${userId}/status`, { method: 'PATCH', token, body: { is_active } })

export const listConversations = (token) => apiFetch('/admin/conversations', { token })

export const getConversationMessages = (token, conversationId) =>
  apiFetch(`/admin/conversations/${conversationId}/messages`, { token })

export const deleteConversation = (token, conversationId) =>
  apiFetch(`/admin/conversations/${conversationId}`, { method: 'DELETE', token })

export const listTasks = (token, ownerId) =>
  apiFetch(`/admin/tasks${ownerId ? `?owner_id=${encodeURIComponent(ownerId)}` : ''}`, { token })

export const deleteTask = (token, taskId) =>
  apiFetch(`/admin/tasks/${taskId}`, { method: 'DELETE', token })

export const listReminders = (token, ownerId) =>
  apiFetch(`/admin/reminders${ownerId ? `?owner_id=${encodeURIComponent(ownerId)}` : ''}`, { token })

export const deleteReminder = (token, reminderId) =>
  apiFetch(`/admin/reminders/${reminderId}`, { method: 'DELETE', token })

export const listMemories = (token, ownerId) =>
  apiFetch(`/admin/memories${ownerId ? `?owner_id=${encodeURIComponent(ownerId)}` : ''}`, { token })

export const deleteMemory = (token, memoryId) =>
  apiFetch(`/admin/memories/${memoryId}`, { method: 'DELETE', token })
