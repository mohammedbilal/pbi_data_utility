import { useState, useRef, useEffect } from 'react'
import LiveOutput from '../components/LiveOutput.jsx'
import { useRunState } from '../hooks/useRunState.js'
import { addHistory } from '../api.js'

// Deal-level DEAL_TYPE (spec §20.3.2). Selecting one constrains the deal's issuer
// sector, sub-industry, and internal asset profile; blank = random per deal.
const DEAL_TYPES = ['ABS', 'CLO', 'CMBS', 'RMBS']

const LS_KEY = 'pbi.securitized'
function lsRead() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {} } catch { return {} } }

export default function SecuritizedTab({ config, activeEnvName, prefill, onPrefillConsumed }) {
  const defaults = config?.tool_defaults?.securitized || {}
  const refDirs = config?.reference_dirs || {}
  const pf = prefill || {}
  const ls = lsRead()

  // The three *_per_* params are cycling lists (§20.9): the form holds them as
  // comma-separated text and parses on run, exactly as LoansTab does for
  // tranches_per_multi.
  const initList = (src, fallback) =>
    Array.isArray(src) ? src.join(', ') : String(src ?? fallback)

  const [deals, setDeals] = useState(pf.deals ?? ls.deals ?? defaults.deals ?? 1)
  const [seriesPerDeal, setSeriesPerDeal] = useState(
    pf.series_per_deal != null ? initList(pf.series_per_deal, '1') :
    ls.series_per_deal != null ? ls.series_per_deal : initList(defaults.series_per_deal, '1')
  )
  const [tranchesPerSeries, setTranchesPerSeries] = useState(
    pf.tranches_per_series != null ? initList(pf.tranches_per_series, '3') :
    ls.tranches_per_series != null ? ls.tranches_per_series : initList(defaults.tranches_per_series, '3')
  )
  const [securitiesPerTranche, setSecuritiesPerTranche] = useState(
    pf.securities_per_tranche != null ? initList(pf.securities_per_tranche, '2') :
    ls.securities_per_tranche != null ? ls.securities_per_tranche : initList(defaults.securities_per_tranche, '2')
  )
  const [currency, setCurrency] = useState(pf.currency ?? ls.currency ?? defaults.currency ?? '')
  const [couponType, setCouponType] = useState(pf.coupon_type ?? ls.coupon_type ?? defaults.coupon_type ?? 'Fixed')
  const [dealType, setDealType] = useState(pf.deal_type ?? ls.deal_type ?? defaults.deal_type ?? '')
  const [delay, setDelay] = useState(pf.delay ?? ls.delay ?? defaults.delay ?? 1.0)
  const [dryRun, setDryRun] = useState(pf.dry_run ?? ls.dry_run ?? false)

  useEffect(() => { if (prefill) onPrefillConsumed?.() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        deals, series_per_deal: seriesPerDeal, tranches_per_series: tranchesPerSeries,
        securities_per_tranche: securitiesPerTranche, currency, coupon_type: couponType,
        deal_type: dealType, delay, dry_run: dryRun,
      }))
    } catch {}
  }, [deals, seriesPerDeal, tranchesPerSeries, securitiesPerTranche, currency,
      couponType, dealType, delay, dryRun])

  const paramsRef = useRef(null)
  const envRef = useRef(activeEnvName)
  envRef.current = activeEnvName

  function recordRun(core) {
    const p = paramsRef.current
    if (!p) return
    addHistory({
      ts: new Date().toISOString(),
      tool: 'securitized',
      env: envRef.current,
      params_summary: `${p.deals} deals · ${p.series_per_deal.join(', ')} series · ` +
        `${p.tranches_per_series.join(', ')} tranches · ${p.deal_type || 'random type'} · ` +
        `${p.currency || 'random'} · ${p.coupon_type}`,
      params_raw: p,
      ok: core.ok, fail: core.fail, total: core.total,
      status: core.status, dur_seconds: core.durSeconds,
    }).catch(() => {})
  }

  const { running, logs, summary, error, runId, status, start, stop } =
    useRunState('securitized', recordRun)

  function parseList(text, fallback) {
    const list = String(text).split(',').map(s => parseInt(s.trim(), 10)).filter(Boolean)
    return list.length ? list : fallback
  }

  function buildParams() {
    return {
      deals: Number(deals),
      series_per_deal: parseList(seriesPerDeal, [1]),
      tranches_per_series: parseList(tranchesPerSeries, [3]),
      securities_per_tranche: parseList(securitiesPerTranche, [2]),
      currency: currency.trim().toUpperCase(),
      coupon_type: couponType,
      deal_type: dealType,
      delay: Number(delay),
      dry_run: dryRun,
      ref_dir: refDirs.securitized || '',
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
              <label>Deals</label>
              <input type="number" min="0" value={deals}
                onChange={e => setDeals(e.target.value)} />
              <div className="field-hint">one deal = one POST of the whole tree</div>
            </div>
            <div className="field">
              <label>Series per deal</label>
              <input type="text" value={seriesPerDeal}
                onChange={e => setSeriesPerDeal(e.target.value)}
                placeholder="e.g. 1, 2" />
              <div className="field-hint">cycles across deals · realistic max 4</div>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label>Tranches per series</label>
              <input type="text" value={tranchesPerSeries}
                onChange={e => setTranchesPerSeries(e.target.value)}
                placeholder="e.g. 3, 5" />
              <div className="field-hint">cycles across all series · realistic 3–6</div>
            </div>
            <div className="field">
              <label>Securities per tranche</label>
              <input type="text" value={securitiesPerTranche}
                onChange={e => setSecuritiesPerTranche(e.target.value)}
                placeholder="e.g. 2" />
              <div className="field-hint">1 or 2 — anything else is clamped</div>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label>Currency</label>
              <select value={currency} onChange={e => setCurrency(e.target.value)}>
                <option value="">random per deal</option>
                <option value="USD">USD</option>
                <option value="EUR">EUR</option>
                <option value="GBP">GBP</option>
              </select>
            </div>
            <div className="field">
              <label>Coupon type</label>
              <select value={couponType} onChange={e => setCouponType(e.target.value)}>
                <option value="Fixed">Fixed</option>
                <option value="Float">Float — benchmark + spread, no rate/yield</option>
                <option value="Mixed">Mixed — random per tranche</option>
              </select>
            </div>
          </div>

          <div className="field">
            <label>Deal type</label>
            <select value={dealType} onChange={e => setDealType(e.target.value)}>
              <option value="">random per deal</option>
              {DEAL_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <div className="field-hint">
              Drives the issuer sector, sub-industry, and the deal's asset profile
              (industry, pool statistics, prepayment convention and use of proceeds).
            </div>
          </div>

          <div className="field">
            <label>Delay (s)</label>
            <input type="number" min="0" step="0.5" value={delay}
              onChange={e => setDelay(e.target.value)} />
            <div className="field-hint">between deal POSTs</div>
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
