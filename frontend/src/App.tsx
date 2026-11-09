import { useEffect } from 'react'
import { Toast } from '@dsh/primitives'
import { AppShell } from './layout/AppShell'
import { AuthOverlay } from './views/AuthOverlay'
import { SettingsPage } from './views/SettingsPage'
import { boot, useStore } from './store'

export function App() {
  const booted = useStore(s => s.booted)
  const authRequired = useStore(s => s.authRequired)
  const settingsOpen = useStore(s => s.settingsOpen)
  const toasts = useStore(s => s.toasts)

  useEffect(() => { void boot() }, [])

  if (authRequired) return <AuthOverlay />
  if (!booted) {
    return (
      <div className="boot-screen">
        <span className="boot-logo" aria-hidden>墨</span>
        <span>墨记 · 论文智能体</span>
      </div>
    )
  }

  return (
    <>
      <AppShell />
      {settingsOpen && <SettingsPage />}
      <div className="toast-layer">
        {toasts.map(t => (
          <Toast key={t.id} text={t.text} tone={t.kind === 'ok' ? 'success' : undefined} onDone={() => {}} />
        ))}
      </div>
    </>
  )
}
