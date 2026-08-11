// Thin wrapper around the browser's Notification API. Only responsible for the API itself
// (support check, permission, firing one) - deciding *when* to notify (preference on? tab
// hidden?) is business logic that belongs to the caller (AppLayout.jsx), not here.

export function isNotificationSupported() {
  // Safari iOS and most mobile browsers outside an installed PWA have no `Notification`
  // constructor at all (only reachable via a Service Worker's registration.showNotification) -
  // false there is correct, not a bug to work around.
  return typeof window !== 'undefined' && 'Notification' in window
}

export function getNotificationPermission() {
  // 'unsupported' (not a real Notification.permission value) lets callers skip their own
  // isNotificationSupported() check before reading this.
  return isNotificationSupported() ? Notification.permission : 'unsupported'
}

export async function requestNotificationPermission() {
  // Must be called from inside a real user gesture (click/change handler) - the browser silently
  // ignores/ auto-resolves requests made outside one. Enforcing that is the caller's job; this
  // function can't detect it.
  if (!isNotificationSupported()) return 'unsupported'
  // Older Safari only has the callback form, not the Promise-returning one.
  if (Notification.requestPermission.length === 0) return Notification.requestPermission()
  return new Promise(resolve => Notification.requestPermission(resolve))
}

export function notifyTaskSuggested(task, { onClick } = {}) {
  // Double-check right before calling the real API - don't trust the caller already checked.
  if (!isNotificationSupported() || Notification.permission !== 'granted') return null
  try {
    const notification = new Notification('Orbit spotted a commitment', {
      body: task.title,
      // Same tag = same task across multiple open tabs/windows replaces the previous
      // notification instead of stacking duplicates - free dedup at the platform level.
      tag: `task-suggested-${task.id}`,
    })
    notification.onclick = () => {
      window.focus()
      onClick?.()
      notification.close()
    }
    return notification
  } catch {
    // Some Android WebView/PWA contexts throw here even though 'Notification' in window is
    // true (usable only via a Service Worker there) - never let that break the caller.
    return null
  }
}
