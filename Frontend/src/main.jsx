import React from 'react'
import ReactDOM from 'react-dom/client'
import { GoogleOAuthProvider } from '@react-oauth/google'
import 'bootstrap/dist/css/bootstrap.min.css'
import 'bootstrap-icons/font/bootstrap-icons.css'
import 'bootstrap/dist/js/bootstrap.bundle.min.js'
import './styles.css'
import './assistant.css'
import AppRouter from './router/AppRouter'
import { AuthProvider } from './context/AuthContext'

// Empty clientId just disables the Google button's provider context (GoogleLogin quietly
// no-ops/errors on click instead of crashing at import time) when GOOGLE_OAUTH is unset - dev
// without a Google Cloud OAuth client configured still works for the existing email/password flow.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <GoogleOAuthProvider clientId={import.meta.env.VITE_GOOGLE_CLIENT_ID || ''}>
      <AuthProvider><AppRouter /></AuthProvider>
    </GoogleOAuthProvider>
  </React.StrictMode>
)
