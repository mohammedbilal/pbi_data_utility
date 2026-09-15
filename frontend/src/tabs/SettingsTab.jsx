import { useState, useEffect } from 'react'
import { saveConfig, testCompareDb } from '../api.js'

const COMPARE_DEFAULTS = {
  store_path: '', ingest_window_minutes: 60, expected_datasource: 'LLM',
  auto_capture: true,
  date_tolerance_days: { default: 0, maturity_date: 3 },
}

// spec §20.9.1. The three *_per_* values are cycling lists stored as arrays in
// environments.json; the inputs hold them as comma-separated text.
const SECURITIZED_DEFAULTS = {
  deals: 1, series_per_deal: [1], tranches_per_series: [3],
  securities_per_tranche: [2], currency: '', coupon_type: 'Fixed',
  deal_type: '', delay: 1.0,
}
const listToText = v => Array.isArray(v) ? v.join(', ') : String(v ?? '')
const textToList = (text, fallback) => {
  const list = String(text).split(',').map(s => parseInt(s.trim(), 10)).filter(Boolean)
  return list.length ? list : fallback
}

export default function SettingsTab({ config, refreshConfig, onToast }) {
  const [envs, setEnvs]           = useState({})
  const [refDirs, setRefDirs]     = useState({})
  const [emailCfg, setEmailCfg]   = useState({ recipient: '', save_copy_dir: '' })
  const [compareCfg, setCompareCfg] = useState(COMPARE_DEFAULTS)
  const [toolDefaults, setToolDefaults] = useState({})
  const [selectedEnv, setSelectedEnv] = useState('')
  const [error, setError]         = useState(null)
  const [dbTest, setDbTest]       = useState(null)   // {ok, reason} of the last Test connection

  useEffect(() => {
    if (!config) return
    setEnvs(config.environments || {})
    setRefDirs(config.reference_dirs || {})
    setEmailCfg({ recipient: '', save_copy_dir: '', ...(config.email || {}) })
    setCompareCfg({ ...COMPARE_DEFAULTS, ...(config.compare || {}) })
    setToolDefaults(config.tool_defaults || {})
    setSelectedEnv(prev => prev || config.active || Object.keys(config.environments || {})[0] || '')
  }, [config])

  useEffect(() => { setDbTest(null) }, [selectedEnv])

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
        db: {
          enabled: false, host: '', port: 5432, database: '', schema: 'public',
          user: '', password: '', sslmode: 'prefer', mirror_enabled: false,
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

  function currentConfig() {
    return { ...config, environments: envs, reference_dirs: refDirs,
             email: emailCfg, compare: compareCfg, tool_defaults: toolDefaults }
  }

  // Only the securitized block is editable here; every other tool's defaults
  // pass through untouched from the loaded config.
  const secDefaults = { ...SECURITIZED_DEFAULTS, ...(toolDefaults.securitized || {}) }
  function updateSecDefault(key, value) {
    setToolDefaults(prev => ({
      ...prev,
      securitized: { ...SECURITIZED_DEFAULTS, ...(prev.securitized || {}), [key]: value },
    }))
  }

  async function handleSave() {
    setError(null)
    try {
      await saveConfig(currentConfig())
      refreshConfig()
      onToast?.(`Saved · ${selectedEnv}`)
    } catch (err) {
      setError(err.message)
    }
  }

  // The probe runs server-side against the *stored* settings, so anything typed
  // has to be persisted first — otherwise "Test connection" would silently test
  // the previous host.
  async function handleTestDb() {
    setError(null)
    setDbTest({ pending: true })
    try {
      await saveConfig(currentConfig())
      refreshConfig()
      const res = await testCompareDb(selectedEnv)
      setDbTest(res)
    } catch (err) {
      setDbTest({ ok: false, reason: err.detail || err.message })
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
            <div className="field">
              <label>Securitized reference dir</label>
              <input type="text"
                value={refDirs.securitized || ''}
                onChange={e => setRefDirs(p => ({ ...p, securitized: e.target.value }))} />
              <div className="field-hint">
                Folder containing entities.csv, asset_types.csv, vocabularies.csv, etc.
                Also backs the Asset type dropdown on the Securitized tab.
              </div>
            </div>

            <p className="section-title">Securitized (ABS) defaults</p>
            <div className="cred-group">
              <div className="field-row">
                <div className="field">
                  <label>Deals</label>
                  <input type="number" min="0" value={secDefaults.deals ?? 1}
                    onChange={e => updateSecDefault('deals', Number(e.target.value) || 0)} />
                </div>
                <div className="field">
                  <label>Delay (s)</label>
                  <input type="number" min="0" step="0.5" value={secDefaults.delay ?? 1}
                    onChange={e => updateSecDefault('delay', Number(e.target.value) || 0)} />
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label>Series per deal</label>
                  <input type="text" value={listToText(secDefaults.series_per_deal)}
                    onChange={e => updateSecDefault('series_per_deal', textToList(e.target.value, [1]))}
                    placeholder="e.g. 1, 2" />
                </div>
                <div className="field">
                  <label>Tranches per series</label>
                  <input type="text" value={listToText(secDefaults.tranches_per_series)}
                    onChange={e => updateSecDefault('tranches_per_series', textToList(e.target.value, [3]))}
                    placeholder="e.g. 3, 5" />
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label>Securities per tranche</label>
                  <input type="text" value={listToText(secDefaults.securities_per_tranche)}
                    onChange={e => updateSecDefault('securities_per_tranche', textToList(e.target.value, [2]))}
                    placeholder="1 or 2" />
                  <div className="field-hint">comma-separated lists cycle across the run</div>
                </div>
                <div className="field">
                  <label>Coupon type</label>
                  <select value={secDefaults.coupon_type || 'Fixed'}
                    onChange={e => updateSecDefault('coupon_type', e.target.value)}>
                    {['Fixed', 'Float', 'Mixed'].map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label>Currency</label>
                  <select value={secDefaults.currency || ''}
                    onChange={e => updateSecDefault('currency', e.target.value)}>
                    <option value="">random per deal</option>
                    {['USD', 'EUR', 'GBP'].map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
                <div className="field">
                  <label>Deal type</label>
                  <select value={secDefaults.deal_type || ''}
                    onChange={e => updateSecDefault('deal_type', e.target.value)}>
                    <option value="">random per deal</option>
                    {['ABS', 'CLO', 'CMBS', 'RMBS'].map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <div className="field-hint">drives issuer sector, sub-industry, and asset profile</div>
                </div>
              </div>
            </div>

            <p className="section-title">Email (broker-email generation)</p>
            <div className="field">
              <label>Recipient</label>
              <input type="text"
                value={emailCfg.recipient || ''}
                onChange={e => setEmailCfg(p => ({ ...p, recipient: e.target.value }))}
                placeholder="e.g. mohammed.bilal@genesis.global" />
              <div className="field-hint">Generated broker emails are sent here via Outlook Classic. Swap for the dedicated parsing mailbox once it exists.</div>
            </div>
            <div className="field">
              <label>Save .msg copy to folder (optional)</label>
              <input type="text"
                value={emailCfg.save_copy_dir || ''}
                onChange={e => setEmailCfg(p => ({ ...p, save_copy_dir: e.target.value }))}
                placeholder="blank = don't save a local copy" />
              <div className="field-hint">If set, each sent email is also saved as a .msg here.</div>
            </div>

            <p className="section-title">Database (read-only) — for Email Compare</p>
            <div className="cred-group">
              <div className="toggle-row">
                <label>Enabled — offer "Fetch from DB" for this environment</label>
                <label className="toggle">
                  <input type="checkbox" checked={env.db?.enabled === true}
                    onChange={e => updateEnvField('db.enabled', e.target.checked)} />
                  <span className="toggle-slider" />
                </label>
              </div>
              <div className="field-row">
                <div className="field">
                  <label>Host</label>
                  <input type="text" value={env.db?.host || ''}
                    onChange={e => updateEnvField('db.host', e.target.value)}
                    placeholder="e.g. qa-pbi-db.cddev.genesis.global" />
                </div>
                <div className="field">
                  <label>Port</label>
                  <input type="number" value={env.db?.port ?? 5432}
                    onChange={e => updateEnvField('db.port', Number(e.target.value) || 5432)} />
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label>Database</label>
                  <input type="text" value={env.db?.database || ''}
                    onChange={e => updateEnvField('db.database', e.target.value)}
                    placeholder="e.g. postgres" />
                  {env.db?.enabled && !env.db?.database && (
                    <div className="field-hint" style={{ color: 'var(--attention)' }}>
                      Required while Enabled is on — without it Fetch from DB stays
                      disabled. Saving overwrites whatever is stored, so fill this in
                      before pressing Test connection.
                    </div>
                  )}
                </div>
                <div className="field">
                  <label>Schema</label>
                  <input type="text" value={env.db?.schema || 'public'}
                    onChange={e => updateEnvField('db.schema', e.target.value)} />
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label>User</label>
                  <input type="text" value={env.db?.user || ''}
                    onChange={e => updateEnvField('db.user', e.target.value)} />
                  <div className="field-hint">must be read-only — the utility never writes here</div>
                </div>
                <div className="field">
                  <label>Password</label>
                  <input type="password" value={env.db?.password || ''}
                    onChange={e => updateEnvField('db.password', e.target.value)} />
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label>SSL mode</label>
                  <select value={env.db?.sslmode || 'prefer'}
                    onChange={e => updateEnvField('db.sslmode', e.target.value)}>
                    {['disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full']
                      .map(m => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
                <div className="field">
                  <label>Mirror util_* tables here</label>
                  <select value={env.db?.mirror_enabled ? 'yes' : 'no'}
                    onChange={e => updateEnvField('db.mirror_enabled', e.target.value === 'yes')}>
                    <option value="no">no — SQLite only</option>
                    <option value="yes">yes — also write the mirror</option>
                  </select>
                </div>
              </div>
              <div className="btn-row" style={{ marginTop: 12 }}>
                <button className="btn btn-ghost btn-sm" onClick={handleTestDb}>
                  {dbTest?.pending ? 'Testing…' : '⇄ Test connection'}
                </button>
              </div>
              {dbTest && !dbTest.pending && (
                <div className={`alert alert-${dbTest.ok ? 'success' : 'warn'}`} style={{ marginTop: 10 }}>
                  <div>{dbTest.ok ? `Connected — ${dbTest.target}` : dbTest.reason}</div>
                  {dbTest.ok && dbTest.missing_tables?.length > 0 && (
                    <div className="alert-line">
                      not visible to this account: {dbTest.missing_tables.join(', ')}
                    </div>
                  )}
                </div>
              )}
              <div className="field-hint" style={{ marginTop: 8 }}>
                Testing saves your changes first, because the probe runs server-side against the
                stored settings. Needs <code>psycopg</code> installed — without it the Email
                Compare tab falls back to CSV import and says so.
              </div>
            </div>

            <p className="section-title">Comparison (Email Compare)</p>
            <div className="cred-group">
              <div className="field-row">
                <div className="field">
                  <label>Expectation store path</label>
                  <input type="text" value={compareCfg.store_path || ''}
                    onChange={e => setCompareCfg(p => ({ ...p, store_path: e.target.value }))}
                    placeholder="blank = backend/expected/pbi_util.db" />
                </div>
                <div className="field">
                  <label>Ingest window (minutes)</label>
                  <input type="number" min="1" value={compareCfg.ingest_window_minutes ?? 60}
                    onChange={e => setCompareCfg(p => ({ ...p, ingest_window_minutes: Number(e.target.value) || 60 }))} />
                  <div className="field-hint">how long after the email a fetch still looks</div>
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label>Expected datasource (agent lane)</label>
                  <input type="text" value={compareCfg.expected_datasource || ''}
                    onChange={e => setCompareCfg(p => ({ ...p, expected_datasource: e.target.value }))} />
                  <div className="field-hint">stamped on email-ingested rows → actual</div>
                </div>
                <div className="field">
                  <label>Maturity date tolerance (days)</label>
                  <input type="number" min="0"
                    value={compareCfg.date_tolerance_days?.maturity_date ?? 3}
                    onChange={e => setCompareCfg(p => ({
                      ...p, date_tolerance_days: { ...p.date_tolerance_days,
                        maturity_date: Number(e.target.value) || 0 },
                    }))} />
                  <div className="field-hint">every other date compares exactly</div>
                </div>
              </div>
              <div className="toggle-row">
                <label>Auto-capture — write expectations whenever email generation is on</label>
                <label className="toggle">
                  <input type="checkbox" checked={compareCfg.auto_capture !== false}
                    onChange={e => setCompareCfg(p => ({ ...p, auto_capture: e.target.checked }))} />
                  <span className="toggle-slider" />
                </label>
              </div>
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
