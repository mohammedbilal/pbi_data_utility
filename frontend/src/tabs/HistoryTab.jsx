import { useState, useEffect } from 'react'
import { getHistory } from '../api.js'

const TOOL_LABEL = { bonds: 'Bonds', loans: 'Loans', interest: 'Interest', tig_orders: 'TIG Orders', csv_upload: 'CSV Upload' }

function formatTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  if (isNaN(d)) return '—'
  const now = new Date()
  const hhmm = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  const sameDay = d.toDateString() === now.toDateString()
  const yest = new Date(now); yest.setDate(now.getDate() - 1)
  const isYest = d.toDateString() === yest.toDateString()
  if (sameDay) return hhmm
  if (isYest) return `Yest ${hhmm}`
  return `${d.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit' })} ${hhmm}`
}

function formatDur(seconds) {
  const s = Number(seconds) || 0
  if (s < 60) return `${s.toFixed(1)}s`
  return `${Math.floor(s / 60)}m${String(Math.round(s % 60)).padStart(2, '0')}`
}

export default function HistoryTab({ onRerun }) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getHistory()
      .then(data => setRows(Array.isArray(data) ? data : []))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="history-view">
      <div className="history-head">
        <span className="history-head-title">run history</span>
        <span className="history-head-sub">{rows.length} runs · last 30 days</span>
      </div>

      <div className="history-body">
        <div className="history-grid history-head-row">
          <span>time</span><span>tool</span><span>env</span><span>params</span>
          <span>result</span><span>status</span><span>dur</span><span />
        </div>

        {loading ? (
          <div className="history-empty">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="history-empty">No runs yet — completed runs will appear here.</div>
        ) : (
          rows.map(r => (
            <div key={r.id} className={`history-grid history-row${r.status === 'stopped' ? ' stopped' : ''}`}>
              <span className="h-time">{formatTime(r.ts)}</span>
              <span className={`h-tool-${r.tool}`}>{TOOL_LABEL[r.tool] || r.tool}</span>
              <span className="h-env">{r.env || '—'}</span>
              <span className="h-params" title={r.params_summary}>{r.params_summary}</span>
              <span>
                <span className="h-ok">{r.ok}</span>
                <span className="h-sep"> / </span>
                <span className={r.fail > 0 ? 'h-fail' : 'h-zero'}>{r.fail}</span>
              </span>
              <span className={`h-status ${r.status}`}>
                <span className="h-status-dot" />{r.status}
              </span>
              <span className="h-dur">{formatDur(r.dur_seconds)}</span>
              <button
                className="h-rerun"
                title="re-run (prefills params)"
                onClick={() => onRerun(r.tool, r.params_raw)}
              >↻</button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
