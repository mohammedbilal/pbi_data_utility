import { useState, useEffect } from 'react'
import { saveConfig } from '../api.js'

export default function SettingsTab({ config, refreshConfig, onToast }) {
  const [envs, setEnvs]           = useState({})
  const [refDirs, setRefDirs]     = useState({})
  const [selectedEnv, setSelectedEnv] = useState('')
  const [error, setError]         = useState(null)

  useEffect(() => {
    if (!config) return
    setEnvs(config.environments || {})
    setRefDirs(config.reference_dirs || {})
    setSelectedEnv(prev => prev || config.active || Object.keys(config.environments || {})[0] || '')
  }, [config])

  const envNames = Object.keys(envs)
  const env = envs[selectedEnv] || {}

  function updateEnvField(path, value) {
    setEnvs(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      const parts = path.split('.')
      let cur = next[selectedEnv]
      for (let i = 0; i < parts.length - 1; i++) {
        if (!cur[parts[i]]) cur[parts[i]] = {}
        cur = cur[parts[i]]
      }
      cur[parts[parts.length - 1]] = value
      return next
    })
  }

  function addEnvironment() {
    const name = prompt('Name for the new environment:')?.trim()
    if (!name || envs[name]) return
    setEnvs(prev => ({
      ...prev,
      [name]: {
        host_name: '',
        verify_ssl: true,
        credentials: {
          bonds_loans: { username: '', password: '' },
          interest:    { username: '', password: '' },
          tig_orders:  { username: '', password: '' },
        },
      },
    }))
    setSelectedEnv(name)
  }

  function deleteEnvironment() {
    if (envNames.length <= 1) { alert('Cannot delete the last environment.'); return }
    if (!confirm(`Delete environment "${selectedEnv}"?`)) return
    const next = { ...envs }
    delete next[selectedEnv]
    setEnvs(next)
    setSelectedEnv(Object.keys(next)[0])
  }

  async function handleSave() {
    setError(null)
    try {
      const updated = { ...config, environments: envs, reference_dirs: refDirs }
      await saveConfig(updated)
      refreshConfig()
      onToast?.(`Saved · ${selectedEnv}`)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="settings-layout">
      {/* ── Env list ── */}
      <div className="settings-sidebar">
        <div className="settings-section-label">Environments</div>
        {envNames.map(name => (
          <div
            key={name}
            className={`env-list-item${selectedEnv === name ? ' selected' : ''}`}
            onClick={() => setSelectedEnv(name)}
          >
            <div style={{ minWidth: 0 }}>
              <div className="env-list-item-name">{name}</div>
              <div className="env-list-item-host">{envs[name]?.host_name || '—'}</div>
            </div>
            {config?.active === name && <span className="env-active-tag">ACTIVE</span>}
          </div>
        ))}
        <button className="btn btn-ghost env-add-btn" onClick={addEnvironment}>
          + Add Environment
        </button>
      </div>

      {/* ── Env detail ── */}
      <div className="settings-detail">
        {selectedEnv ? (
          <>
            <div className="settings-title-row">
              <span className="settings-title">{selectedEnv}</span>
              {config?.active === selectedEnv && <span className="settings-active-pill">active</span>}
            </div>

            {error && <div className="alert alert-error">{error}</div>}

            <p className="section-title">Connection</p>
            <div className="field">
              <label>Host name</label>
              <input type="text" value={env.host_name || ''}
                onChange={e => updateEnvField('host_name', e.target.value)}
                placeholder="e.g. qa-trowe-pbi2.cddev.genesis.global" />
            </div>
            <div className="toggle-row">
              <label>Verify SSL</label>
              <label className="toggle">
                <input type="checkbox"
                  checked={env.verify_ssl !== false}
                  onChange={e => updateEnvField('verify_ssl', e.target.checked)} />
                <span className="toggle-slider" />
              </label>
            </div>

            <p className="section-title">Bonds &amp; Loans credentials</p>
            <div className="cred-group">
              <div className="field-row">
                <div className="field">
                  <label>Username</label>
                  <input type="text"
                    value={env.credentials?.bonds_loans?.username || ''}
                    onChange={e => updateEnvField('credentials.bonds_loans.username', e.target.value)} />
                </div>
                <div className="field">
                  <label>Password</label>
                  <input type="password"
                    value={env.credentials?.bonds_loans?.password || ''}
                    onChange={e => updateEnvField('credentials.bonds_loans.password', e.target.value)} />
                </div>
              </div>
            </div>

            <p className="section-title">Interest Capture credentials</p>
            <div className="cred-group">
              <div className="field-row">
                <div className="field">
                  <label>Username</label>
                  <input type="text"
                    value={env.credentials?.interest?.username || ''}
                    onChange={e => updateEnvField('credentials.interest.username', e.target.value)} />
                </div>
                <div className="field">
                  <label>Password</label>
                  <input type="password"
                    value={env.credentials?.interest?.password || ''}
                    onChange={e => updateEnvField('credentials.interest.password', e.target.value)} />
                </div>
              </div>
            </div>

            <p className="section-title">Create TIG Orders credentials</p>
            <div className="cred-group">
              <div className="field-row">
                <div className="field">
                  <label>Username</label>
                  <input type="text"
                    value={env.credentials?.tig_orders?.username || ''}
                    onChange={e => updateEnvField('credentials.tig_orders.username', e.target.value)} />
                </div>
                <div className="field">
                  <label>Password</label>
                  <input type="password"
                    value={env.credentials?.tig_orders?.password || ''}
                    onChange={e => updateEnvField('credentials.tig_orders.password', e.target.value)} />
                </div>
              </div>
            </div>

            <p className="section-title">Reference data paths</p>
            <div className="field">
              <label>Bonds reference dir</label>
              <input type="text"
                value={refDirs.bonds || ''}
                onChange={e => setRefDirs(p => ({ ...p, bonds: e.target.value }))} />
              <div className="field-hint">Folder containing issuers.csv, currencies.csv, etc.</div>
            </div>
            <div className="field">
              <label>Loans reference dir</label>
              <input type="text"
                value={refDirs.loans || ''}
                onChange={e => setRefDirs(p => ({ ...p, loans: e.target.value }))} />
              <div className="field-hint">reference_data/ containing issuers.csv and currencies.csv.</div>
            </div>

            <div className="btn-row" style={{ margin: '18px 0 16px', flex: 'none' }}>
              <button className="btn btn-primary" style={{ flex: 'none', padding: '10px 18px' }}
                onClick={handleSave}>Save Changes</button>
            </div>

            <div className="danger-zone">
              <div className="danger-zone-title">Danger zone</div>
              <button className="btn btn-danger btn-sm" onClick={deleteEnvironment}>
                Delete "{selectedEnv}"
              </button>
            </div>
          </>
        ) : (
          <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: 40 }}>
            Select an environment from the list.
          </div>
        )}
      </div>
    </div>
  )
}
