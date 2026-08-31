import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import Layout from './components/Layout'
import { useAuth } from './store/auth'
import { getT, useLang } from './i18n'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Profile from './pages/Profile'
import Ask from './pages/Ask'
import Facts from './pages/Facts'
import Health from './pages/Health'
import TimelinePage from './pages/TimelinePage'
import GraphPage from './pages/GraphPage'
import FocusPage from './pages/Focus'
import Conversations from './pages/Conversations'
import Settings from './pages/Settings'
import Privacy from './pages/Privacy'

export default function App() {
  const apiKey = useAuth((s) => s.apiKey)
  const lang = useLang((s) => s.lang)

  // Keep the document chrome (lang for a11y/SEO, tab title) in sync with the chosen language.
  useEffect(() => {
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'
    document.title = getT().meta.title
  }, [lang])

  if (!apiKey) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Login />} />
      </Routes>
    )
  }

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="profile" element={<Profile />} />
        <Route path="ask" element={<Ask />} />
        <Route path="facts" element={<Facts />} />
        <Route path="health" element={<Health />} />
        <Route path="timeline" element={<TimelinePage />} />
        <Route path="graph" element={<GraphPage />} />
        <Route path="focus" element={<FocusPage />} />
        <Route path="conversations" element={<Conversations />} />
        <Route path="settings" element={<Settings />} />
        <Route path="privacy" element={<Privacy />} />
      </Route>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
