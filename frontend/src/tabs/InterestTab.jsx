import { useState, useRef, useEffect } from 'react'
import LiveOutput from '../components/LiveOutput.jsx'
import { useRunState } from '../hooks/useRunState.js'
import { addHistory } from '../api.js'

export default function InterestTab({ config, activeEnvName, prefill, onPrefillConsumed }) {
  const d = config?.tool_defaults?.interest || {}
  const qr = d.quantity_ranges || {}
  const sz = d.sizing || {}
  const pf = prefill || {}
  const pfqr = pf.quantity_ranges || {}
  const pfsz = pf.sizing || {}

  const [trancheName, setTrancheName]   = useState(pf.tranche_name ?? d.tranche_name ?? '')
  const [trueCount, setTrueCount]       = useState(pf.is_modelled_true ?? d.is_modelled_true ?? 9)
  const [falseCount, setFalseCount]     = useState(pf.is_modelled_false ?? d.is_modelled_false ?? 5)
  const [closeness, setCloseness]       = useState(pf.pair_closeness_pct ?? d.pair_closeness_pct ?? 2)
  const [mismatch, setMismatch]         = useState(pf.total_mismatch_pct ?? d.total_mismatch_pct ?? 0)
  const [delaySeconds, setDelaySeconds] = useState(pf.delay_seconds ?? d.delay_seconds ?? 4)
  const [strategy, setStrategy]         = useState(pf.strategy ?? d.strategy ?? false)
  const [entity, setEntity]             = useState(pf.event_defaults?.ENTITY ?? d.event_defaults?.ENTITY ?? 'TRPA')
  const [minPiece, setMinPiece]         = useState(pfsz.min_piece ?? sz.min_piece ?? 10000)
  const [increment, setIncrement]       = useState(pfsz.increment_size ?? sz.increment_size ?? 100000)
  const [trueMin, setTrueMin]           = useState(pfqr.true_min ?? qr.true_min ?? 100000)
  const [trueMax, setTrueMax]           = useState(pfqr.true_max ?? qr.true_max ?? 1200000)
  const [falseMin, setFalseMin]         = useState(pfqr.false_min ?? qr.false_min ?? 600000)
  const [falseMax, setFalseMax]         = useState(pfqr.false_max ?? qr.false_max ?? 1600000)
  const [dryRun, setDryRun]             = useState(pf.dry_run ?? false)

  useEffect(() => { if (prefill) onPrefillConsumed?.() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const paramsRef = useRef(null)
  const envRef = useRef(activeEnvName)
  envRef.current = activeEnvName

  function recordRun(core) {
    const p = paramsRef.current
    if (!p) return
    addHistory({
      ts: new Date().toISOString(),
      tool: 'interest',
      env: envRef.current,
      params_summary: `${p.event_defaults?.ENTITY || entity} · ${p.is_modelled_true} true · ${p.is_modelled_false} false`,
      params_raw: p,
      ok: core.ok, fail: core.fail, total: core.total,
      status: core.status, dur_seconds: core.durSeconds,
    }).catch(() => {})
  }

  const { running, logs, summary, error, runId, status, start, stop } = useRunState('interest', recordRun)

  function buildParams() {
    return {
      tranche_name: trancheName,
      is_modelled_true: Number(trueCount),
      is_modelled_false: Number(falseCount),
      pair_closeness_pct: Number(closeness),
      total_mismatch_pct: Number(mismatch),
      delay_seconds: Number(delaySeconds),
      strategy,
      dry_run: dryRun,
      is_modelled_as_string: true,
      sizing: {
        min_piece: Number(minPiece),
        increment_size: Number(increment),
      },
      quantity_ranges: {
        true_min: Number(trueMin),
        true_max: Number(trueMax),
        false_min: Number(falseMin),
        false_max: Number(falseMax),
      },
      event_defaults: { ...(d.event_defaults || {}), ENTITY: entity },
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

          <div className="field">
            <label>Tranche name (UUID)</label>
            <input type="text" value={trancheName}
              onChange={e => setTrancheName(e.target.value)}
              placeholder="UUID of the bond issuance" />
          </div>

          <div className="field-row">
            <div className="field">
              <label>Entity</label>
              <select value={entity} onChange={e => setEntity(e.target.value)}>
                <option value="TRPA">TRPA</option>
                <option value="TRPIM">TRPIM</option>
              </select>
            </div>
            <div className="field">
              <label>Delay (s)</label>
              <input type="number" min="0" step="0.5" value={delaySeconds}
                onChange={e => setDelaySeconds(e.target.value)} />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label>Modelled = true</label>
              <input type="number" min="0" value={trueCount}
                onChange={e => setTrueCount(e.target.value)} />
            </div>
            <div className="field">
              <label>Modelled = false</label>
              <input type="number" min="0" value={falseCount}
                onChange={e => setFalseCount(e.target.value)} />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label>Pair closeness %</label>
              <input type="number" min="0" step="0.5" value={closeness}
                onChange={e => setCloseness(e.target.value)} />
            </div>
            <div className="field">
              <label>Total mismatch %</label>
              <input type="number" min="0" step="0.5" value={mismatch}
                onChange={e => setMismatch(e.target.value)} />
            </div>
          </div>

          <div className="toggle-row bordered">
            <label>Strategy mode (parent/child)</label>
            <label className="toggle">
              <input type="checkbox" checked={strategy}
                onChange={e => setStrategy(e.target.checked)} />
              <span className="toggle-slider" />
            </label>
          </div>

          <p className="section-title">Sizing rules</p>
          <div className="field-row">
            <div className="field">
              <label>Min piece</label>
              <input type="number" min="0" value={minPiece}
                onChange={e => setMinPiece(e.target.value)} />
            </div>
            <div className="field">
              <label>Increment</label>
              <input type="number" min="0" value={increment}
                onChange={e => setIncrement(e.target.value)} />
            </div>
          </div>

          <p className="section-title">Quantity ranges</p>
          <div className="field-row">
            <div className="field">
              <label>True min</label>
              <input type="number" min="0" value={trueMin}
                onChange={e => setTrueMin(e.target.value)} />
            </div>
            <div className="field">
              <label>True max</label>
              <input type="number" min="0" value={trueMax}
                onChange={e => setTrueMax(e.target.value)} />
            </div>
          </div>
          <div className="field-row">
            <div className="field">
              <label>False min</label>
              <input type="number" min="0" value={falseMin}
                onChange={e => setFalseMin(e.target.value)} />
            </div>
            <div className="field">
              <label>False max</label>
              <input type="number" min="0" value={falseMax}
                onChange={e => setFalseMax(e.target.value)} />
            </div>
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
