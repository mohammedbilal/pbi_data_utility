import { useState, useRef, useEffect } from 'react'
import LiveOutput from '../components/LiveOutput.jsx'
import { useRunState } from '../hooks/useRunState.js'
import { addHistory } from '../api.js'

const LS_KEY = 'pbi.bonds'
function lsRead() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {} } catch { return {} } }

export default function BondsTab({ config, activeEnvName, prefill, onPrefillConsumed }) {
  const defaults = config?.tool_defaults?.bonds || {}
  const refDirs = config?.reference_dirs || {}
  const pf = prefill || {}
  const ls = lsRead()

  const initTranches = (src, fallback) =>
    Array.isArray(src) ? src.join(', ') : String(src ?? fallback)

  const [single, setSingle] = useState(pf.single ?? ls.single ?? defaults.single ?? 0)
  const [multi, setMulti] = useState(pf.multi ?? ls.multi ?? defaults.multi ?? 1)
  const [tranches, setTranches] = useState(
    pf.tranches_per_multi != null ? initTranches(pf.tranches_per_multi, '3') :
    ls.tranches != null ? ls.tranches : initTranches(defaults.tranches_per_multi, '3')
  )
  const [sleepMs, setSleepMs] = useState(pf.sleep_ms ?? ls.sleep_ms ?? defaults.sleep_ms ?? 100)
  const [currency, setCurrency] = useState(pf.currency ?? ls.currency ?? '')
  const [dryRun, setDryRun] = useState(pf.dry_run ?? ls.dry_run ?? false)

  useEffect(() => { if (prefill) onPrefillConsumed?.() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify({ single, multi, tranches, sleep_ms: sleepMs, currency, dry_run: dryRun })) } catch {}
  }, [single, multi, tranches, sleepMs, currency, dryRun])

  const paramsRef = useRef(null)
  const envRef = useRef(activeEnvName)
  envRef.current = activeEnvName

  function recordRun(core) {
    const p = paramsRef.current
    if (!p) return
    addHistory({
      ts: new Date().toISOString(),
      tool: 'bonds',
      env: envRef.current,
      params_summary: `${p.single} single · ${p.multi} multi · ${p.tranches_per_multi.join(', ')}`,
      params_raw: p,
      ok: core.ok, fail: core.fail, total: core.total,
      status: core.status, dur_seconds: core.durSeconds,
    }).catch(() => {})
  }

  const { running, logs, summary, error, runId, status, start, stop } = useRunState('bonds', recordRun)

  function buildParams() {
    const trancheList = tranches.split(',').map(s => parseInt(s.trim(), 10)).filter(Boolean)
    return {
      single: Number(single),
      multi: Number(multi),
      tranches_per_multi: trancheList.length ? trancheList : [3],
      sleep_ms: Number(sleepMs),
      currency: currency.trim().toUpperCase() || null,
      dry_run: dryRun,
      ref_dir: refDirs.bonds || '',
    }
  }

  function run() {
    const p = buildParams()
    paramsRef.current = p
    start(p)
  }

  return (
    <div className="tab-content">
      <div className="card">
        <div className="card-header">Run parameters</div>
        <div className="card-body">
          {error && <div className="alert alert-error">{error}</div>}

          <div className="field-row">
            <div className="field">
              <label>Single-tranche deals</label>
              <input type="number" min="0" value={single}
                onChange={e => setSingle(e.target.value)} />
            </div>
            <div className="field">
              <label>Multi-tranche deals</label>
              <input type="number" min="0" value={multi}
                onChange={e => setMulti(e.target.value)} />
            </div>
          </div>

          <div className="field">
            <label>Tranches per multi-deal</label>
            <input type="text" value={tranches}
              onChange={e => setTranches(e.target.value)}
              placeholder="e.g. 3, 8" />
            <div className="field-hint">comma-separated — cycles through for each multi-deal</div>
          </div>

          <div className="field">
            <label>Force currency (optional)</label>
            <input type="text" value={currency} maxLength={3}
              onChange={e => setCurrency(e.target.value)}
              placeholder="random"
              style={{ textTransform: 'uppercase' }} />
            <div className="field-hint">e.g., USD, EUR, GBP</div>
          </div>

          <div className="field">
            <label>Delay (ms)</label>
            <input type="number" min="0" value={sleepMs}
              onChange={e => setSleepMs(e.target.value)} />
          </div>

          <div className="toggle-row">
            <label>Dry run — generate only, no POST</label>
            <label className="toggle">
              <input type="checkbox" checked={dryRun}
                onChange={e => setDryRun(e.target.checked)} />
              <span className="toggle-slider" />
            </label>
          </div>

          <div className="btn-row">
            <button className="btn btn-primary" disabled={running} onClick={run}>▶ Run</button>
            <button className="btn btn-danger" disabled={!running} onClick={stop}>■ Stop</button>
          </div>
        </div>
      </div>

      <LiveOutput logs={logs} running={running} summary={summary} runId={runId} status={status} />
    </div>
  )
}
