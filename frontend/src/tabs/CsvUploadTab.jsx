import { useState, useRef, useEffect, useCallback } from 'react'
import LiveOutput from '../components/LiveOutput.jsx'
import { useRunState } from '../hooks/useRunState.js'
import { startCsvRun, addHistory } from '../api.js'

const LS_KEY = 'pbi.csv_upload'
function lsRead() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {} } catch { return {} } }

const ACCEPTED = '.csv,.xlsx,.xls'

export default function CsvUploadTab({ config, activeEnvName, prefill, onPrefillConsumed }) {
  const pf = prefill || {}
  const ls = lsRead()

  const [rows, setRows]           = useState(pf.rows ?? ls.rows ?? '')
  const [delay, setDelay]         = useState(pf.delay_seconds ?? ls.delay_seconds ?? 2)
  const [simulation, setSimulation] = useState(pf.simulation ?? ls.simulation ?? false)
  const [timeScale, setTimeScale] = useState(pf.time_scale ?? ls.time_scale ?? 1)
  const [dryRun, setDryRun]       = useState(pf.dry_run ?? ls.dry_run ?? false)
  const [selectedFile, setSelectedFile] = useState(null)
  const [dragOver, setDragOver]   = useState(false)
  const fileInputRef = useRef(null)

  useEffect(() => { if (prefill) onPrefillConsumed?.() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        rows, delay_seconds: delay, simulation, time_scale: timeScale, dry_run: dryRun,
      }))
    } catch {}
  }, [rows, delay, simulation, timeScale, dryRun])

  const paramsRef = useRef(null)
  const envRef = useRef(activeEnvName)
  envRef.current = activeEnvName

  function recordRun(core) {
    const p = paramsRef.current
    if (!p) return
    addHistory({
      ts: new Date().toISOString(),
      tool: 'csv_upload',
      env: envRef.current,
      params_summary: `${p.file_name}${p.rows ? ` · rows ${p.rows}` : ''}`,
      params_raw: { ...p, file_bytes: undefined },
      ok: core.ok, fail: core.fail, total: core.total,
      status: core.status, dur_seconds: core.durSeconds,
    }).catch(() => {})
  }

  const startFn = useCallback(async () => {
    if (!selectedFile) throw new Error('No file selected')
    const params = buildParams()
    paramsRef.current = { ...params, file_name: selectedFile.name }
    return startCsvRun(selectedFile, params)
  }, [selectedFile, rows, delay, simulation, timeScale, dryRun]) // eslint-disable-line react-hooks/exhaustive-deps

  const { running, logs, summary, error, runId, status, start, stop } =
    useRunState('csv_upload', recordRun, startFn)

  function buildParams() {
    return {
      rows: rows.trim(),
      delay_seconds: Number(delay),
      simulation: simulation ? 'Y' : 'N',
      time_scale: Number(timeScale),
      dry_run: dryRun,
    }
  }

  function run() { start({}) }

  function pickFile(f) {
    if (!f) return
    const ext = f.name.split('.').pop().toLowerCase()
    if (!['csv', 'xlsx', 'xls'].includes(ext)) return
    setSelectedFile(f)
  }

  function onInputChange(e) { pickFile(e.target.files?.[0]) }

  function onDrop(e) {
    e.preventDefault()
    setDragOver(false)
    pickFile(e.dataTransfer.files?.[0])
  }

  function clearFile(e) {
    e.stopPropagation()
    setSelectedFile(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  return (
    <div className="tab-content">
      <div className="card">
        <div className="card-header">Run parameters</div>
        <div className="card-body">
          {error && <div className="alert alert-error">{error}</div>}

          {/* Drop zone */}
          <div
            className={`drop-zone${dragOver ? ' drag-over' : ''}`}
            onClick={() => !selectedFile && fileInputRef.current?.click()}
            onDragOver={e => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED}
              onChange={onInputChange}
              onClick={e => e.stopPropagation()}
            />
            <div className="drop-zone-icon">📂</div>
            {selectedFile ? (
              <div className="drop-zone-file">
                <span>{selectedFile.name}</span>
                <button title="Remove file" onClick={clearFile}>✕</button>
              </div>
            ) : (
              <div className="drop-zone-label">
                Drop a <strong>CSV or Excel</strong> file here, or <strong>click to browse</strong>
              </div>
            )}
          </div>

          <div className="field-row">
            <div className="field">
              <label>Row range (optional)</label>
              <input type="text" value={rows}
                onChange={e => setRows(e.target.value)}
                placeholder="e.g. 2-5  (blank = all)" />
              <div className="field-hint">1-based, inclusive — leave blank to process every row</div>
            </div>
            <div className="field">
              <label>Delay (s)</label>
              <input type="number" min="0" step="0.5" value={delay}
                onChange={e => setDelay(e.target.value)} />
              <div className="field-hint">pause between rows (fixed or fallback)</div>
            </div>
          </div>

          <div className="toggle-row bordered">
            <label>Simulation mode — replay by INSERT_TIME</label>
            <label className="toggle">
              <input type="checkbox" checked={simulation}
                onChange={e => setSimulation(e.target.checked)} />
              <span className="toggle-slider" />
            </label>
          </div>

          {simulation && (
            <div className="field" style={{ marginTop: 10 }}>
              <label>Time scale</label>
              <input type="number" min="0.1" step="0.1" value={timeScale}
                onChange={e => setTimeScale(e.target.value)} />
              <div className="field-hint">1 = real-time · 2 = 2× faster · 0.5 = half speed</div>
            </div>
          )}

          <div className="toggle-row">
            <label>Dry run — generate only, no POST</label>
            <label className="toggle">
              <input type="checkbox" checked={dryRun}
                onChange={e => setDryRun(e.target.checked)} />
              <span className="toggle-slider" />
            </label>
          </div>

          <div className="btn-row">
            <button className="btn btn-primary" disabled={running || !selectedFile} onClick={run}>
              ▶ Run
            </button>
            <button className="btn btn-danger" disabled={!running} onClick={stop}>■ Stop</button>
          </div>
          {!selectedFile && !running && (
            <div className="field-hint" style={{ marginTop: 6, textAlign: 'center' }}>
              Select a file to enable Run
            </div>
          )}
        </div>
      </div>

      <LiveOutput logs={logs} running={running} summary={summary} runId={runId} status={status} />
    </div>
  )
}
