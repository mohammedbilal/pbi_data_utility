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
  const [emailOn, setEmailOn] = useState(pf.email_on ?? ls.email_on ?? false)
  const [emailOnly, setEmailOnly] = useState(pf.email_only ?? ls.email_only ?? false)
  const [emailFormat, setEmailFormat] = useState(pf.email_format ?? ls.email_format ?? 'bond_stacked')
  // Expectation capture (spec §18). Both default on whenever email generation
  // is on — capture_expected follows compare.auto_capture, and a minted ticker
  // per deal is what makes a captured row matchable later (§18.7 / D8).
  const autoCapture = config?.compare?.auto_capture !== false
  const [captureExpected, setCaptureExpected] =
    useState(pf.capture_expected ?? ls.capture_expected ?? autoCapture)
  const [uniqueTicker, setUniqueTicker] =
    useState(pf.unique_ticker ?? ls.unique_ticker ?? true)

  useEffect(() => { if (prefill) onPrefillConsumed?.() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify({ single, multi, tranches, sleep_ms: sleepMs, currency, dry_run: dryRun, email_on: emailOn, email_only: emailOnly, email_format: emailFormat, capture_expected: captureExpected, unique_ticker: uniqueTicker })) } catch {}
  }, [single, multi, tranches, sleepMs, currency, dryRun, emailOn, emailOnly, emailFormat, captureExpected, uniqueTicker])

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
      email_on: emailOn,
      email_only: emailOnly,
      email_mode: emailOn ? (emailOnly ? 'email_only' : 'both') : 'off',
      email_format: emailFormat,
      ref_dir: refDirs.bonds || '',
      // Only sent when email generation is on: there is no expectation to
      // capture without an email, and an explicit `true` would override the
      // engine's own "email must be on" default.
      ...(emailOn ? { capture_expected: captureExpected,
                      unique_ticker: captureExpected && uniqueTicker } : {}),
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
                  <option value="bond_stacked">Stacked block — deal header + per-tranche table (Oracle/SpaceX)</option>
                  <option value="bond_single">Single labelled block (Synchrony/PSNH)</option>
                  <option value="bond_colon">Compact colon-inline, per-tenor blocks (Amazon CAD)</option>
                  <option value="bond_inline">Inline single-line rows + IPTs header (SMBC)</option>
                  <option value="bond_narrative">Investor-call narrative + stacked table (SpaceX)</option>
                </select>
                <div className="field-hint">Recipient is set in Settings → Email. Sends via Outlook Classic.</div>
              </div>

              <div className="toggle-row">
                <label>Capture expectations — write the expected DB state for each email</label>
                <label className="toggle">
                  <input type="checkbox" checked={captureExpected}
                    onChange={e => setCaptureExpected(e.target.checked)} />
                  <span className="toggle-slider" />
                </label>
              </div>

              {captureExpected && (
                <div className="toggle-row">
                  <label>Unique ticker per deal — each issuer keeps its own reference ticker</label>
                  <label className="toggle">
                    <input type="checkbox" checked={uniqueTicker}
                      onChange={e => setUniqueTicker(e.target.checked)} />
                    <span className="toggle-slider" />
                  </label>
                </div>
              )}
              {captureExpected && !uniqueTicker && (
                <div className="field-hint" style={{ marginTop: -4, color: 'var(--attention)' }}>
                  Off, nothing enforces uniqueness: two deals can draw the same issuer, and rows
                  from earlier runs of that issuer can be matched by mistake (§18.7).
                </div>
              )}
              <div className="field-hint" style={{ marginTop: 6 }}>
                Captured runs are scored in the <strong>Email Compare</strong> tab. Capture is
                SQLite-only — it works in dry-run and never fails a run.
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
