import { useState, useRef, useEffect } from 'react'
import LiveOutput from '../components/LiveOutput.jsx'
import { useRunState } from '../hooks/useRunState.js'
import { addHistory } from '../api.js'

const LS_KEY = 'pbi.loans'
function lsRead() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {} } catch { return {} } }

export default function LoansTab({ config, activeEnvName, prefill, onPrefillConsumed }) {
  const defaults = config?.tool_defaults?.loans || {}
  const refDirs = config?.reference_dirs || {}
  const pf = prefill || {}
  const ls = lsRead()

  const initTranches = (src, fallback) =>
    Array.isArray(src) ? src.join(', ') : String(src ?? fallback)

  const [single, setSingle] = useState(pf.single ?? ls.single ?? defaults.single ?? 3)
  const [multi, setMulti] = useState(pf.multi ?? ls.multi ?? defaults.multi ?? 0)
  const [tranches, setTranches] = useState(
    pf.tranches_per_multi != null ? initTranches(pf.tranches_per_multi, '2, 3') :
    ls.tranches != null ? ls.tranches : initTranches(defaults.tranches_per_multi, '2, 3')
  )
  const [delay, setDelay] = useState(pf.delay ?? ls.delay ?? defaults.delay ?? 15)
  const [dealQueryWait, setDealQueryWait] = useState(pf.deal_query_wait ?? ls.deal_query_wait ?? defaults.deal_query_wait ?? 6)
  const [currency, setCurrency] = useState(pf.currency ?? ls.currency ?? '')
  const [dryRun, setDryRun] = useState(pf.dry_run ?? ls.dry_run ?? false)
  const [emailOn, setEmailOn] = useState(pf.email_on ?? ls.email_on ?? false)
  const [emailOnly, setEmailOnly] = useState(pf.email_only ?? ls.email_only ?? false)
  const [emailFormat, setEmailFormat] = useState(pf.email_format ?? ls.email_format ?? 'loan_jpm')

  useEffect(() => { if (prefill) onPrefillConsumed?.() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify({ single, multi, tranches, delay, deal_query_wait: dealQueryWait, currency, dry_run: dryRun, email_on: emailOn, email_only: emailOnly, email_format: emailFormat })) } catch {}
  }, [single, multi, tranches, delay, dealQueryWait, currency, dryRun, emailOn, emailOnly, emailFormat])

  const paramsRef = useRef(null)
  const envRef = useRef(activeEnvName)
  envRef.current = activeEnvName

  function recordRun(core) {
    const p = paramsRef.current
    if (!p) return
    addHistory({
      ts: new Date().toISOString(),
      tool: 'loans',
      env: envRef.current,
      params_summary: `${p.single} single · ${p.multi} multi · ${p.tranches_per_multi.join(', ')}`,
      params_raw: p,
      ok: core.ok, fail: core.fail, total: core.total,
      status: core.status, dur_seconds: core.durSeconds,
    }).catch(() => {})
  }

  const { running, logs, summary, error, runId, status, start, stop } = useRunState('loans', recordRun)

  function buildParams() {
    const trancheList = tranches.split(',').map(s => parseInt(s.trim(), 10)).filter(Boolean)
    return {
      single: Number(single),
      multi: Number(multi),
      tranches_per_multi: trancheList.length ? trancheList : [2, 3],
      delay: Number(delay),
      deal_query_wait: Number(dealQueryWait),
      currency: currency.trim().toUpperCase() || null,
      dry_run: dryRun,
      email_on: emailOn,
      email_only: emailOnly,
      email_mode: emailOn ? (emailOnly ? 'email_only' : 'both') : 'off',
      email_format: emailFormat,
      ref_dir: refDirs.loans || '',
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
              placeholder="e.g. 2, 3" />
          </div>

          <div className="field-row">
            <div className="field">
              <label>Delay (s)</label>
              <input type="number" min="0" step="0.5" value={delay}
                onChange={e => setDelay(e.target.value)} />
            </div>
            <div className="field">
              <label>Query wait (s)</label>
              <input type="number" min="0" step="0.5" value={dealQueryWait}
                onChange={e => setDealQueryWait(e.target.value)} />
            </div>
          </div>

          <div className="field">
            <label>Force currency</label>
            <input type="text" value={currency} maxLength={3}
              onChange={e => setCurrency(e.target.value)}
              placeholder="random"
              style={{ textTransform: 'uppercase' }} />
          </div>

          <div className="toggle-row">
            <label>Dry run — generate only, no POST</label>
            <label className="toggle">
              <input type="checkbox" checked={dryRun}
                onChange={e => setDryRun(e.target.checked)} />
              <span className="toggle-slider" />
            </label>
          </div>

          <div className="toggle-row">
            <label>Email — generate broker email &amp; send to Settings recipient</label>
            <label className="toggle">
              <input type="checkbox" checked={emailOn}
                onChange={e => setEmailOn(e.target.checked)} />
              <span className="toggle-slider" />
            </label>
          </div>

          {emailOn && (
            <>
              <div className="toggle-row">
                <label>Email only — no API POST (just send the email)</label>
                <label className="toggle">
                  <input type="checkbox" checked={emailOnly}
                    onChange={e => setEmailOnly(e.target.checked)} />
                  <span className="toggle-slider" />
                </label>
              </div>
              <div className="field">
                <label>Email format</label>
                <select value={emailFormat} onChange={e => setEmailFormat(e.target.value)}>
                  <option value="loan_jpm">JPM Loan Launch (COMPANY/BORROWER/BUSINESS/UOP)</option>
                  <option value="loan_barclays">Barclays Lead Left / Calendar (+ Commitments Due)</option>
                  <option value="loan_citi">Citi Loan (Borrower/Facility)</option>
                  <option value="loan_rbc">RBC Debut TLB (narrative + Business/Sponsor)</option>
                </select>
                <div className="field-hint">Recipient is set in Settings → Email. Sends via Outlook Classic.</div>
              </div>
            </>
          )}

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
