import { createContext, useContext, useEffect, useState } from 'react'
import * as authApi from '../api/auth'

const TOKEN_KEY = 'orbit_token'
const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY))
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!token) { setUser(null); setLoading(false); return }
    authApi.getMe(token)
      .then(setUser)
      .catch(() => { localStorage.removeItem(TOKEN_KEY); setToken(null); setUser(null) })
      .finally(() => setLoading(false))
  }, [token])

  const login = async (email, password) => {
    const data = await authApi.login({ email, password })
    localStorage.setItem(TOKEN_KEY, data.access_token)
    setUser(data.user)
    setToken(data.access_token)
  }

  const register = async (email, password, display_name) => {
    const data = await authApi.register({ email, password, display_name })
    localStorage.setItem(TOKEN_KEY, data.access_token)
    setUser(data.user)
    setToken(data.access_token)
  }

  // Handles both first-time signup and returning login transparently (find-or-create on the
  // backend) - same as login/register above, just fed a Google ID token instead of a password.
  const loginWithGoogle = async (idToken) => {
    const data = await authApi.googleAuth(idToken)
    localStorage.setItem(TOKEN_KEY, data.access_token)
    setUser(data.user)
    setToken(data.access_token)
  }

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY)
    setToken(null)
    setUser(null)
  }

  const updateProfile = async (updates) => {
    const updated = await authApi.updateProfile(token, updates)
    setUser(updated)
    return updated
  }

  const changePassword = (passwords) => authApi.changePassword(token, passwords)

  const isAdmin = user?.role === 'admin'

  return (
    <AuthContext.Provider
      value={{ user, token, loading, isAdmin, login, register, loginWithGoogle, logout, updateProfile, changePassword }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
