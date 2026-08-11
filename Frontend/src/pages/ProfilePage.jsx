import { useState } from 'react'
import PageHeader from '../components/common/PageHeader'
import SettingsSection from '../components/profile/SettingsSection'
import { useAuth } from '../context/AuthContext'
import { getNotificationPermission, isNotificationSupported, requestNotificationPermission } from '../utils/browserNotifications'

const DEFAULT_PREFS = { language: 'English', default_reminder_lead: '30 minutes before', desktop_notifications: true, email_digest: true, ai_suggestion_alerts: false, permission_scope: '20 latest messages', ai_response_detail: 'Balanced', personalized_suggestions: true }

export default function ProfilePage(){
  const { user, updateProfile, changePassword } = useAuth()
  const [form, setForm] = useState(() => ({ display_name: user?.display_name || '', job_title: user?.job_title || '', timezone: user?.timezone || 'Asia/Ho_Chi_Minh', ...DEFAULT_PREFS, ...(user?.preferences || {}) }))
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [pw, setPw] = useState({ current_password: '', new_password: '' })
  const [pwStatus, setPwStatus] = useState('')
  const [pwSaving, setPwSaving] = useState(false)
  const [notifPermission, setNotifPermission] = useState(() => getNotificationPermission())

  const set = key => e => setForm(f => ({ ...f, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))

  // "AI suggestion alerts" also drives a real browser Notification (AppLayout.jsx) when this tab
  // is backgrounded, not just the preference toggle - so turning it on is the one moment we have
  // a real user gesture to ask the browser for permission. Re-asking after 'granted'/'denied' is
  // a no-op (browser resolves it immediately without prompting again), so skip while not 'default'.
  const handleAiAlertsToggle = async e => {
    const next = e.target.checked
    if (next && isNotificationSupported() && getNotificationPermission() === 'default') {
      setNotifPermission(await requestNotificationPermission())
    }
    setForm(f => ({ ...f, ai_suggestion_alerts: next }))
  }

  const save = async () => {
    setSaving(true); setError('')
    try {
      const { display_name, job_title, timezone, ...preferences } = form
      await updateProfile({ display_name, job_title, timezone, preferences })
      setSaved(true); setTimeout(() => setSaved(false), 1800)
    } catch (err) { setError(err.detail || 'Could not save changes.') }
    finally { setSaving(false) }
  }

  const submitPassword = async () => {
    setPwSaving(true); setPwStatus('')
    try {
      await changePassword(pw)
      setPw({ current_password: '', new_password: '' })
      setPwStatus('Password updated.')
    } catch (err) { setPwStatus(err.detail || 'Could not change password.') }
    finally { setPwSaving(false) }
  }

  const initials = (user?.display_name || '?').trim().split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase()
  const filled = [form.display_name, form.job_title, form.timezone, user?.email].filter(Boolean).length
  const completion = Math.round((filled / 4) * 100)

  return <div className="page-container profile-page"><PageHeader eyebrow="Account" title="Profile & settings" description="Manage your personal details and Orbit preferences." action={<button className="btn btn-primary" onClick={save} disabled={saving}><i className={`bi ${saved?'bi-check2':'bi-floppy'} me-2`}/>{saving?'Saving...':saved?'Saved':'Save changes'}</button>}/>
    {error && <div className="auth-error">{error}</div>}
    <div className="profile-layout"><aside className="profile-card"><div className="profile-avatar">{initials}</div><h2>{user?.display_name}</h2><p>{user?.email}</p><span>{form.job_title || 'No job title set'}</span><div className="profile-completion"><div><strong>Profile completion</strong><b>{completion}%</b></div><div className="progress"><div className="progress-bar" style={{width:`${completion}%`}}/></div></div><nav><a href="#basic" className="active"><i className="bi bi-person"/>Basic information</a><a href="#preferences"><i className="bi bi-sliders"/>Preferences</a><a href="#notifications"><i className="bi bi-bell"/>Notifications</a><a href="#ai"><i className="bi bi-stars"/>AI settings</a><a href="#security"><i className="bi bi-shield-lock"/>Security</a></nav></aside><div className="settings-stack">
      <SettingsSection icon="bi-person" title="Basic information" description="Your personal details and contact information."><div className="form-grid"><label><span>Full name</span><input className="form-control" value={form.display_name} onChange={set('display_name')}/></label><label><span>Email address</span><input className="form-control" value={user?.email || ''} disabled/></label><label><span>Job title</span><input className="form-control" value={form.job_title} onChange={set('job_title')}/></label><label><span>Timezone</span><select className="form-select" value={form.timezone} onChange={set('timezone')}><option value="Asia/Ho_Chi_Minh">Bangkok/Hanoi (GMT+7)</option><option value="Europe/London">London (GMT+0)</option><option value="America/New_York">New York (GMT-5)</option></select></label></div></SettingsSection>
      <SettingsSection icon="bi-sliders" title="Preferences" description="Customize how Orbit works for you."><div className="form-grid"><label><span>Preferred language</span><select className="form-select" value={form.language} onChange={set('language')}><option>English</option><option>Tiếng Việt</option><option>Thai</option><option>Spanish</option></select></label><label><span>Default reminder time</span><select className="form-select" value={form.default_reminder_lead} onChange={set('default_reminder_lead')}><option>30 minutes before</option><option>15 minutes before</option><option>1 hour before</option></select></label></div></SettingsSection>
      <SettingsSection icon="bi-bell" title="Notifications" description="Choose where and when you want to be notified."><SettingToggle title="Desktop notifications" detail="Tasks, reminders, and mentions" checked={form.desktop_notifications} onChange={set('desktop_notifications')}/><SettingToggle title="Email digest" detail="A daily summary delivered at 8:00 AM" checked={form.email_digest} onChange={set('email_digest')}/><SettingToggle title="AI suggestion alerts" detail="When Orbit finds a new task or event" checked={form.ai_suggestion_alerts} onChange={handleAiAlertsToggle} warning={!form.ai_suggestion_alerts ? null : notifPermission === 'denied' ? "Browser notifications are blocked for this site - enable them in your browser's site settings to get alerts while this tab is in the background." : notifPermission === 'unsupported' ? 'Your browser does not support desktop notifications; you will still see in-app alerts.' : null}/></SettingsSection>
      <SettingsSection icon="bi-stars" title="AI settings" description="Control Orbit's access and defaults."><div className="form-grid"><label><span>Default permission scope</span><select className="form-select" value={form.permission_scope} onChange={set('permission_scope')}><option>20 latest messages</option><option>50 latest messages</option><option>Unread messages</option></select></label><label><span>AI response detail</span><select className="form-select" value={form.ai_response_detail} onChange={set('ai_response_detail')}><option>Concise</option><option>Balanced</option><option>Detailed</option></select></label></div><SettingToggle title="Personalized suggestions" detail="Use memory to make AI results more relevant" checked={form.personalized_suggestions} onChange={set('personalized_suggestions')}/></SettingsSection>
      <SettingsSection icon="bi-shield-lock" title="Security" description="Keep your account safe."><div className="security-row"><div><strong>Change password</strong><p>{pwStatus || 'Choose a new password for your account.'}</p></div></div><div className="form-grid"><label><span>Current password</span><input type="password" className="form-control" value={pw.current_password} onChange={e=>setPw(p=>({...p,current_password:e.target.value}))}/></label><label><span>New password</span><input type="password" className="form-control" value={pw.new_password} onChange={e=>setPw(p=>({...p,new_password:e.target.value}))}/></label></div><button className="btn btn-light mt-2" onClick={submitPassword} disabled={pwSaving || !pw.current_password || pw.new_password.length<6}>{pwSaving?'Updating...':'Update password'}</button></SettingsSection>
    </div></div></div>
}

function SettingToggle({title,detail,checked=false,onChange,warning}){return <div className="setting-toggle"><div><strong>{title}</strong><p>{detail}</p>{warning && <p className="text-warning small mb-0"><i className="bi bi-exclamation-triangle me-1"/>{warning}</p>}</div><div className="form-check form-switch"><input className="form-check-input" type="checkbox" checked={checked} onChange={onChange}/></div></div>}
