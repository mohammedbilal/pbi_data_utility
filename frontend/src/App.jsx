import { useState, useEffect, useRef } from 'react'
import Header from './components/Header.jsx'
import BondsTab from './tabs/BondsTab.jsx'
import LoansTab from './tabs/LoansTab.jsx'
import InterestTab from './tabs/InterestTab.jsx'
import HistoryTab from './tabs/HistoryTab.jsx'
import SettingsTab from './tabs/SettingsTab.jsx'
import { getConfig, saveConfig } from './api.js'

const TABS = [
  { id: 'bonds',    label: 'Bonds' },
  { id: 'loans',    label: 'Loans' },
  { id: 'interest', label: 'Interest Capture' },
  { id: 'history',  label: 'History' },
  { id: 'settings', label: 'Settings' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState('bonds')
  const [config, setConfig] = useState(null)
  const [pendingRun, setPendingRun] = useState(null) // { tool, params } for re-run prefill
  const [toast, setToast] = useState(null)
  const toastTimer = useRef(null)

  useEffect(() => {
    getConfig().then(setConfig).catch(console.error)
  }, [])

  const activeEnvName = config?.active || ''
  const activeEnv = config?.environments?.[activeEnvName] || {}
  const envNames = Object.keys(config?.environments || {})

  function showToast(msg) {
    clearTimeout(toastTimer.current)
    setToast(msg)
    toastTimer.current = setTimeout(() => setToast(null), 2400)
  }

  function switchEnv(name) {
    if (!config || name === config.active) return
    const updated = { ...config, active: name }
    setConfig(updated) // optimistic
    saveConfig(updated).catch(err => {
      console.error(err)
      showToast('Could not persist env switch')
    })
  }

  function refreshConfig() {
    getConfig().then(setConfig).catch(console.error)
  }

  // Re-run from History: prefill the tool's params, switch to its tab.
  function rerunFromHistory(tool, params) {
    setPendingRun({ tool, params: params || {} })
    setActiveTab(tool)
  }

  const tabProps = { config, activeEnv, activeEnvName, refreshConfig }

  function prefillFor(id) {
    return pendingRun?.tool === id ? pendingRun.params : null
  }
  const consumePrefill = () => setPendingRun(null)

  return (
    <div className="app-root">
      <Header
        envNames={envNames}
        activeEnvName={activeEnvName}
        activeEnvHost={activeEnv?.host_name || ''}
        onSwitchEnv={switchEnv}
      />

      <div className="tabs-bar">
        {TABS.map(t => (
          <button
            key={t.id}
            className={`tab-btn${activeTab === t.id ? ' active' : ''}`}
            onClick={() => setActiveTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="view">
        {activeTab === 'bonds' && (
          <BondsTab {...tabProps} prefill={prefillFor('bonds')} onPrefillConsumed={consumePrefill} />
        )}
        {activeTab === 'loans' && (
          <LoansTab {...tabProps} prefill={prefillFor('loans')} onPrefillConsumed={consumePrefill} />
        )}
        {activeTab === 'interest' && (
          <InterestTab {...tabProps} prefill={prefillFor('interest')} onPrefillConsumed={consumePrefill} />
        )}
        {activeTab === 'history' && (
          <HistoryTab onRerun={rerunFromHistory} />
        )}
        {activeTab === 'settings' && (
          <SettingsTab {...tabProps} onToast={showToast} />
        )}
      </div>

      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
