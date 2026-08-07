import { apiFetch } from './client'

export const listMemories = (token) => apiFetch('/memories', { token })

export const createMemory = (token, { category, title, detail }) =>
  apiFetch('/memories', { method: 'POST', token, body: { category, title, detail } })

export const updateMemory = (token, memoryId, updates) =>
  apiFetch(`/memories/${memoryId}`, { method: 'PATCH', token, body: updates })

export const deleteMemory = (token, memoryId) =>
  apiFetch(`/memories/${memoryId}`, { method: 'DELETE', token })
