import { StrictMode, useEffect, useState, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router'
import { Layout } from './components/Layout'
import { ToastProvider } from './components/ui'
import './index.css'
import { api } from './lib/api'
import ActivityPage from './pages/Activity'
import Calendar from './pages/Calendar'
import Cockpit from './pages/Cockpit'
import Course from './pages/Course'
import Courses from './pages/Courses'
import Deadlines from './pages/Deadlines'
import Files from './pages/Files'
import Home from './pages/Home'
import Knowledge from './pages/Knowledge'
import Login from './pages/Login'
import Planner from './pages/Planner'
import Settings from './pages/Settings'
import Tasks from './pages/Tasks'

function RequireSession({ children }: { children: ReactNode }) {
  const [state, setState] = useState<'checking' | 'ok' | 'out'>('checking')
  useEffect(() => {
    api.session().then(
      () => setState('ok'),
      () => setState('out'),
    )
  }, [])
  if (state === 'checking') return null
  return state === 'ok' ? children : <Navigate to="/entrar" replace />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ToastProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/entrar" element={<Login />} />
          <Route
            element={
              <RequireSession>
                <Layout />
              </RequireSession>
            }
          >
            <Route index element={<Cockpit />} />
            <Route path="painel" element={<Home />} />
            <Route path="ficheiros" element={<Files />} />
            <Route path="calendario" element={<Calendar />} />
            <Route path="tarefas" element={<Tasks />} />
            <Route path="prazos" element={<Deadlines />} />
            <Route path="estudo" element={<Planner />} />
            <Route path="cadeiras" element={<Courses />} />
            <Route path="cadeiras/:id" element={<Course />} />
            <Route path="conhecimento" element={<Knowledge />} />
            <Route path="atividade" element={<ActivityPage />} />
            <Route path="definicoes" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ToastProvider>
  </StrictMode>,
)
