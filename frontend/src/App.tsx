import { Navigate, Route, Routes } from 'react-router-dom'

import Layout from './components/Layout'
import { useAuth } from './store/auth'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Ask from './pages/Ask'
import Facts from './pages/Facts'
import TimelinePage from './pages/TimelinePage'
import GraphPage from './pages/GraphPage'
import FocusPage from './pages/Focus'
import Conversations from './pages/Conversations'
import Privacy from './pages/Privacy'

export default function App() {
  const apiKey = useAuth((s) => s.apiKey)

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
        <Route path="ask" element={<Ask />} />
        <Route path="facts" element={<Facts />} />
        <Route path="timeline" element={<TimelinePage />} />
        <Route path="graph" element={<GraphPage />} />
        <Route path="focus" element={<FocusPage />} />
        <Route path="conversations" element={<Conversations />} />
        <Route path="privacy" element={<Privacy />} />
      </Route>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
