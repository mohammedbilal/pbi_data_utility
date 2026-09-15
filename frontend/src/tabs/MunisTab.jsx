import { useState, useRef, useEffect } from 'react'
import LiveOutput from '../components/LiveOutput.jsx'
import { useRunState } from '../hooks/useRunState.js'
import { addHistory } from '../api.js'

// The platform's own "Munis Sector" drop-down (spec §21.5.2). Selecting one narrows the
// issuer pool; the issuer then supplies the purpose code, tax status and ratings, so
// there is deliberately no separate control for those.
const SECTORS = [
  'Economic Development', 'Education', 'General Purposes', 'Health Care', 'Housing',
  'Industrial Development', 'Miscellaneous', 'Pension', 'Power', 'Public Buildings',
  'Public Works', 'Recreation', 'Transportation', 'Utilities - Other', 'Water & Sewer',
  'Various',
]

// "Munis Tax Status" — blank means "whatever this issuer actually issues".
const TAX_STATUSES = ['Tax-Exempt', 'Taxable', 'AMT', 'Corp', 'Various']

// "Munis Deal Status". Priced/Expected appear in the source extract but not in the
// platform drop-down, so they are read as derived states and not offered (§21.6.4).
const DEAL_STATUSES = [
  'Not Published', 'Allotments Available', 'Awaiting Allotments', 'Preliminary Pricing',
  'Retail Order Period', 'Verbal Award/CXL Window', 'Day-to-Day', 'Week Of', 'Price Ideas',
]

// States with at least one issuer in reference/munis/issuers.csv.
const STATES = [
  'AR', 'CA', 'CO', 'CT', 'DC', 'FL', 'HI', 'IA', 'IL', 'IN', 'KY', 'LA', 'MA', 'MD',
  'MI', 'NE', 'NH', 'NY', 'OH', 'OR', 'PA', 'RI', 'SC', 'TN', 'TX', 'UT', 'VA', 'WA',
  'WI', 'WY',
]

const LS_KEY = 'pbi.munis'
function lsRead() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {} } catch { return {} } }

export default function MunisTab({ config, activeEnvName, prefill, onPrefillConsumed }) {
  const defaults = config?.tool_defaults?.munis || {}
  const refDirs = config?.reference_dirs || {}
  const pf = prefill || {}
  const ls = lsRead()

  // The three *_per_* params are cycling lists (§21.9): the form holds them as
  // comma-separated text and parses on run, exactly as SecuritizedTab does.
  const initList = (src, fallback) =>
    Array.isArray(src) ? src.join(', ') : String(src ?? fallback)

  const [deals, setDeals] = useState(pf.deals ?? ls.deals ?? defaults.deals ?? 1)
  const [seriesPerDeal, setSeriesPerDeal] = useState(
    pf.series_per_deal != null ? initList(pf.series_per_deal, '2') :
    ls.series_per_deal != null ? ls.series_per_deal : initList(defaults.series_per_deal, '2')
  )
  const [maturitiesPerSeries, setMaturitiesPerSeries] = useState(
    pf.maturities_per_series != null ? initList(pf.maturities_per_series, '8') :
    ls.maturities_per_series != null ? ls.maturities_per_series : initList(defaults.maturities_per_series, '8')
  )
  const [securitiesPerMaturity, setSecuritiesPerMaturity] = useState(
    pf.securities_per_maturity != null ? initList(pf.securities_per_maturity, '1') :
    ls.securities_per_maturity != null ? ls.securities_per_maturity : initList(defaults.securities_per_maturity, '1')
  )
  const [state, setState] = useState(pf.state ?? ls.state ?? defaults.state ?? '')
  const [sector, setSector] = useState(pf.sector ?? ls.sector ?? defaults.sector ?? '')
  const [taxStatus, setTaxStatus] = useState(pf.tax_status ?? ls.tax_status ?? defaults.tax_status ?? '')
  const [dealStatus, setDealStatus] = useState(pf.deal_status ?? ls.deal_status ?? defaults.deal_status ?? '')
  const [roadshow, setRoadshow] = useState(pf.roadshow ?? ls.roadshow ?? defaults.roadshow ?? '')
  const [delay, setDelay] = useState(pf.delay ?? ls.delay ?? defaults.delay ?? 1.0)
  const [dryRun, setDryRun] = useState(pf.dry_run ?? ls.dry_run ?? false)

  useEffect(() => { if (prefill) onPrefillConsumed?.() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        deals, series_per_deal: seriesPerDeal, maturities_per_series: maturitiesPerSeries,
        securities_per_maturity: securitiesPerMaturity, state, sector,
        tax_status: taxStatus, deal_status: dealStatus, roadshow, delay, dry_run: dryRun,
      }))
    } catch {}
  }, [deals, seriesPerDeal, maturitiesPerSeries, securitiesPerMaturity, state, sector,
      taxStatus, dealStatus, roadshow, delay, dryRun])

  const paramsRef = useRef(null)
  const envRef = useRef(activeEnvName)
  envRef.current = activeEnvName

  function recordRun(core) {
    const p = paramsRef.current
    if (!p) return
    addHistory({
      ts: new Date().toISOString(),
      tool: 'munis',
      env: envRef.current,
      params_summary: `${p.deals} deals · ${p.series_per_deal.join(', ')} series · ` +
        `${p.maturities_per_series.join(', ')} maturities · ${p.state || 'any state'} · ` +
        `${p.sector || 'any sector'} · ${p.tax_status || 'issuer tax status'}` +
        `${p.roadshow ? ` · roadshow ${p.roadshow}` : ''}`,
      params_raw: p,
      ok: core.ok, fail: core.fail, total: core.total,
      status: core.status, dur_seconds: core.durSeconds,
    }).catch(() => {})
  }

  const { running, logs, summary, error, runId, status, start, stop } =
    useRunState('munis', recordRun)

  function parseList(text, fallback) {
    const list = String(text).split(',').map(s => parseInt(s.trim(), 10)).filter(Boolean)
    return list.length ? list : fallback
  }

  function buildParams() {
    return {
      deals: Number(deals),
      series_per_deal: parseList(seriesPerDeal, [2]),
      maturities_per_series: parseList(maturitiesPerSeries, [8]),
      securities_per_maturity: parseList(securitiesPerMaturity, [1]),
      state,
      sector,
      tax_status: taxStatus,
      deal_status: dealStatus,
      roadshow,
      delay: Number(delay),
      dry_run: dryRun,
      ref_dir: refDirs.munis || '',
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
                placeholder="e.g. 2, 3" />
              <div className="field-hint">cycles across deals · observed max 7</div>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label>Maturities per series</label>
              <input type="text" value={maturitiesPerSeries}
                onChange={e => setMaturitiesPerSeries(e.target.value)}
                placeholder="e.g. 5, 12" />
              <div className="field-hint">
                the serial ladder — one maturity a year · observed max 31
              </div>
            </div>
            <div className="field">
              <label>Securities per maturity</label>
              <input type="text" value={securitiesPerMaturity}
                onChange={e => setSecuritiesPerMaturity(e.target.value)}
                placeholder="e.g. 1" />
              <div className="field-hint">CUSIPs per maturity · normally 1</div>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label>State</label>
              <select value={state} onChange={e => setState(e.target.value)}>
                <option value="">any — random issuer</option>
                {STATES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="field">
              <label>Sector</label>
              <select value={sector} onChange={e => setSector(e.target.value)}>
                <option value="">any — random issuer</option>
                {SECTORS.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          </div>

          <div className="field">
            <div className="field-hint">
              State and sector pick the <strong>issuer</strong>, and the issuer then supplies
              the purpose code, tax status, ratings, repayment source and credit enhancement —
              so those stay consistent with each other instead of being drawn separately.
              A combination no issuer matches is reported rather than silently ignored.
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label>Tax status</label>
              <select value={taxStatus} onChange={e => setTaxStatus(e.target.value)}>
                <option value="">use the issuer's own</option>
                {TAX_STATUSES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
              <div className="field-hint">
                Taxable / Various also switch the maturity description to a treasury spread
              </div>
            </div>
            <div className="field">
              <label>Deal status</label>
              <select value={dealStatus} onChange={e => setDealStatus(e.target.value)}>
                <option value="">random per deal</option>
                {DEAL_STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <div className="field-hint">constrains the wire type it can pair with</div>
            </div>
          </div>

          <div className="field">
            <label>Roadshow</label>
            <select value={roadshow} onChange={e => setRoadshow(e.target.value)}>
              <option value="">random — ~20% of deals</option>
              <option value="yes">Yes — every deal is a roadshow</option>
              <option value="no">No — no roadshow deals</option>
            </select>
            <div className="field-hint">
              A roadshow deal must carry <code>MANDATE_TEXT</code>, which the engine adds
              automatically; the platform rejects it otherwise (§21.6.13).
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
