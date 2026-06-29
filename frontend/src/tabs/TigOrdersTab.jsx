import { useState, useRef, useEffect } from 'react'
import LiveOutput from '../components/LiveOutput.jsx'
import { useRunState } from '../hooks/useRunState.js'
import { addHistory } from '../api.js'

const LS_KEY = 'pbi.tig_orders'
function lsRead() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {} } catch { return {} } }

export default function TigOrdersTab({ config, activeEnvName, prefill, onPrefillConsumed }) {
  const d = config?.tool_defaults?.tig_orders || {}
  const qr = d.quantity_ranges || {}
  const sz = d.sizing || {}
  const pf = prefill || {}
  const pfqr = pf.quantity_ranges || {}
  const pfsz = pf.sizing || {}
  const ls = lsRead()
  const lsqr = ls.quantity_ranges || {}
  const lssz = ls.sizing || {}

  const [trancheId, setTrancheId]     = useState(pf.tranche_id ?? ls.tranche_id ?? d.tranche_id ?? '')
  const [regionEntity, setRegionEntity] = useState(pf.region_entity ?? ls.region_entity ?? d.region_entity ?? 'AMFR')
  const [marketType, setMarketType]   = useState(pf.market_type ?? ls.market_type ?? d.market_type ?? 'Market')
  const [registration, setRegistration] = useState(pf.registration_type ?? ls.registration_type ?? d.registration_type ?? '144a')
  const [tradeDesk, setTradeDesk]     = useState(pf.trade_desk ?? ls.trade_desk ?? d.trade_desk ?? 'GLOBALFI')
  const [portfolioMgr, setPortfolioMgr] = useState(pf.portfolio_manager ?? ls.portfolio_manager ?? d.portfolio_manager ?? 'John Doe')
  const [count, setCount]             = useState(pf.count ?? ls.count ?? d.count ?? 5)
  const [delaySeconds, setDelaySeconds] = useState(pf.delay_seconds ?? ls.delay_seconds ?? d.delay_seconds ?? 1)
  const [qtyMin, setQtyMin]           = useState(pfqr.min ?? lsqr.min ?? qr.min ?? 100000)
  const [qtyMax, setQtyMax]           = useState(pfqr.max ?? lsqr.max ?? qr.max ?? 1000000)
  const [minPiece, setMinPiece]       = useState(pfsz.min_piece ?? lssz.min_piece ?? sz.min_piece ?? 50000)
  const [increment, setIncrement]     = useState(pfsz.increment ?? lssz.increment ?? sz.increment ?? 50000)
  const [dryRun, setDryRun]           = useState(pf.dry_run ?? ls.dry_run ?? false)

  useEffect(() => { if (prefill) onPrefillConsumed?.() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        tranche_id: trancheId, region_entity: regionEntity, market_type: marketType, registration_type: registration,
        trade_desk: tradeDesk, portfolio_manager: portfolioMgr, count, delay_seconds: delaySeconds,
        dry_run: dryRun,
        quantity_ranges: { min: qtyMin, max: qtyMax },
        sizing: { min_piece: minPiece, increment },
      }))
    } catch {}
  }, [trancheId, regionEntity, marketType, registration, tradeDesk, portfolioMgr, count, delaySeconds, qtyMin, qtyMax, minPiece, increment, dryRun])

  const paramsRef = useRef(null)
  const envRef = useRef(activeEnvName)
  envRef.current = activeEnvName

  function recordRun(core) {
    const p = paramsRef.current
    if (!p) return
    addHistory({
      ts: new Date().toISOString(),
      tool: 'tig_orders',
      env: envRef.current,
      params_summary: `${p.count} order${p.count === 1 ? '' : 's'} · ${p.market_type} · ${p.registration_type}`,
      params_raw: p,
      ok: core.ok, fail: core.fail, total: core.total,
      status: core.status, dur_seconds: core.durSeconds,
    }).catch(() => {})
  }

  const { running, logs, summary, error, runId, status, start, stop } = useRunState('tig_orders', recordRun)

  function buildParams() {
    return {
      tranche_id: trancheId.trim(),
      region_entity: regionEntity,
      market_type: marketType,
      registration_type: registration.trim(),
      trade_desk: tradeDesk.trim(),
      portfolio_manager: portfolioMgr.trim(),
      count: Number(count),
      delay_seconds: Number(delaySeconds),
      dry_run: dryRun,
      quantity_ranges: { min: Number(qtyMin), max: Number(qtyMax) },
      sizing: { min_piece: Number(minPiece), increment: Number(increment) },
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
            <label>Tranche ID</label>
            <input type="text" value={trancheId}
              onChange={e => setTrancheId(e.target.value)}
              placeholder="TRANCHE_ID (entered manually)" />
          </div>

          <div className="field">
            <label>Region entity</label>
            <select value={regionEntity} onChange={e => setRegionEntity(e.target.value)}>
              {['AMFR','AMCH','AMDE','AMEU','AMHK','AMIN','AMPL','AMSG','AMUS'].map(v => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>

          <div className="field">
            <label>Market type</label>
            <select value={marketType} onChange={e => setMarketType(e.target.value)}>
              <option value="Market">Market</option>
              <option value="Limit">Limit</option>
            </select>
          </div>

          <div className="field">
            <label>Portfolio manager (optional)</label>
            <input type="text" value={portfolioMgr}
              onChange={e => setPortfolioMgr(e.target.value)}
              placeholder="e.g. John Doe" />
          </div>

          <div className="field-row">
            <div className="field">
              <label>Registration type</label>
              <input type="text" value={registration}
                onChange={e => setRegistration(e.target.value)}
                placeholder="e.g. 144a" />
            </div>
            <div className="field">
              <label>Trade desk</label>
              <input type="text" value={tradeDesk}
                onChange={e => setTradeDesk(e.target.value)}
                placeholder="e.g. GLOBALFI" />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label>Number of orders</label>
              <input type="number" min="1" value={count}
                onChange={e => setCount(e.target.value)} />
            </div>
            <div className="field">
              <label>Delay (s)</label>
              <input type="number" min="0" step="0.5" value={delaySeconds}
                onChange={e => setDelaySeconds(e.target.value)} />
            </div>
          </div>

          <p className="section-title">Quantity range</p>
          <div className="field-row">
            <div className="field">
              <label>Min</label>
              <input type="number" min="0" value={qtyMin}
                onChange={e => setQtyMin(e.target.value)} />
            </div>
            <div className="field">
              <label>Max</label>
              <input type="number" min="0" value={qtyMax}
                onChange={e => setQtyMax(e.target.value)} />
            </div>
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
          <div className="field-hint">Each order's QUANTITY is a random multiple of the increment, ≥ min piece, within the range. Account codes are auto-generated (6-char uppercase alphanumeric).</div>

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
