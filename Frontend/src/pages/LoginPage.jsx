import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useNavigate } from 'react-router-dom'
import { GoogleLogin } from '@react-oauth/google'
import { useAuth } from '../context/AuthContext'

export default function LoginPage(){const {register,handleSubmit,formState:{errors}}=useForm();const navigate=useNavigate();const {login}=useAuth();const [error,setError]=useState('');const [submitting,setSubmitting]=useState(false)
  const onSubmit=async({email,password})=>{setError('');setSubmitting(true);try{await login(email,password);navigate('/chat')}catch(err){setError(err.detail||'Invalid email or password')}finally{setSubmitting(false)}}
  return <AuthShell title="Welcome back" subtitle="Sign in to continue to your workspace."><form onSubmit={handleSubmit(onSubmit)}>{error&&<div className="auth-error">{error}</div>}<label className="auth-label">Email address</label><div className={`auth-input ${errors.email?'invalid':''}`}><i className="bi bi-envelope"/><input placeholder="you@company.com" {...register('email',{required:true,pattern:/^\S+@\S+\.\S+$/})}/></div>{errors.email&&<small className="text-danger">Enter a valid email address.</small>}<div className="d-flex justify-content-between align-items-center mt-3"><label className="auth-label mb-0">Password</label><button type="button" className="link-button">Forgot password?</button></div><div className={`auth-input ${errors.password?'invalid':''}`}><i className="bi bi-lock"/><input type="password" placeholder="Enter your password" {...register('password',{required:true,minLength:6})}/><i className="bi bi-eye"/></div>{errors.password&&<small className="text-danger">Password must be at least 6 characters.</small>}<label className="remember"><input type="checkbox"/> Remember me</label><button className="btn btn-primary w-100 auth-submit" disabled={submitting}>{submitting?'Signing in...':'Sign in'} <i className="bi bi-arrow-right"/></button><GoogleAuthButton onError={setError}/><p className="auth-switch">New to Orbit? <Link to="/register">Create an account</Link></p></form></AuthShell>}

// Shared by LoginPage and RegisterPage - one Google button, one endpoint on the backend does
// find-or-create, so there's nothing to distinguish "sign in" vs "sign up" with Google here.
export function GoogleAuthButton({ onError }) {
  const navigate = useNavigate()
  const { loginWithGoogle } = useAuth()
  // No Client ID configured (VITE_GOOGLE_CLIENT_ID unset) - password login only. Without this
  // guard <GoogleLogin/> still renders and fails at click time with Google's own confusing
  // "Missing required parameter: client_id" error instead of just not being there.
  if (!import.meta.env.VITE_GOOGLE_CLIENT_ID) return null
  return (
    <>
      <div className="auth-divider">or continue with</div>
      <GoogleLogin
        width="100%"
        onSuccess={(credentialResponse) => {
          onError('')
          loginWithGoogle(credentialResponse.credential)
            .then(() => navigate('/chat'))
            .catch((err) => onError(err.detail || 'Could not sign in with Google'))
        }}
        onError={() => onError('Google sign-in failed')}
      />
    </>
  )
}

export function AuthShell({title,subtitle,children}){return <main className="auth-page"><section className="auth-visual"><div className="auth-brand"><span><i className="bi bi-command"/></span>Orbit</div><div className="visual-copy"><span className="eyebrow-light"><i className="bi bi-stars"/> Your AI work companion</span><h1>Turn every conversation<br/>into <em>action.</em></h1><p>Orbit finds the tasks, meetings, and decisions hidden in your team's daily conversations.</p><div className="auth-feature"><span><i className="bi bi-lightning-charge"/></span><div><strong>Work smarter, not harder</strong><small>Stay focused while Orbit handles the details.</small></div></div></div><div className="visual-orb orb-one"/><div className="visual-orb orb-two"/><div className="visual-quote">“Orbit gives me back an hour every day.”<span>— Jamie, Product Lead</span></div></section><section className="auth-form-side"><div className="auth-mobile-brand"><span><i className="bi bi-command"/></span>Orbit</div><div className="auth-form-card"><h2>{title}</h2><p>{subtitle}</p>{children}</div><small className="auth-legal">By continuing, you agree to our Terms and Privacy Policy.</small></section></main>}
