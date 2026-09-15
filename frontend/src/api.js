const BASE = '/api'

async function _json(res) {
  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try { detail = JSON.parse(text).detail ?? text } catch {}
    const err = new Error(`HTTP ${res.status}: ${detail}`)
    err.status = res.status          // callers distinguish "no pass yet" (404) from a real failure
    err.detail = detail
    throw err
  }
  return res.json()
}

export async function getConfig() {
  return _json(await fetch(`${BASE}/config`))
}

export async function saveConfig(data) {
  return _json(await fetch(`${BASE}/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }))
}

export async function startRun(tool, params) {
  return _json(await fetch(`${BASE}/${tool}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  }))
}

export async function stopRun(tool, runId) {
  return _json(await fetch(`${BASE}/${tool}/stop/${runId}`, { method: 'POST' }))
}

export function createEventSource(tool, runId) {
  return new EventSource(`${BASE}/${tool}/stream/${runId}`)
}

export async function startCsvRun(file, params) {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('params_json', JSON.stringify(params))
  return _json(await fetch(`${BASE}/csv_upload/run`, { method: 'POST', body: fd }))
}

export async function getHistory() {
  return _json(await fetch(`${BASE}/history`))
}

export async function addHistory(record) {
  return _json(await fetch(`${BASE}/history`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(record),
  }))
}

/* ── Email Comparison (spec §18.11) ────────────────────────────────────────
   The compare surface is plain REST — no SSE — because a compare pass is a
   single request, not a run. Every call answers with JSON; `POST /fetch` in
   particular answers 200 with {ok:false, reason} when the database cannot be
   queried, so a missing driver is a UI state and not an error path.        */

const CMP = `${BASE}/compare`

async function _post(url, body) {
  return _json(await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }))
}

export async function getCompareRuns(assetClass = 'bonds', limit = 50) {
  return _json(await fetch(`${CMP}/runs?asset_class=${encodeURIComponent(assetClass)}&limit=${limit}`))
}

export async function getCompareRun(runId) {
  return _json(await fetch(`${CMP}/runs/${runId}`))
}

export async function deleteCompareRun(runId) {
  return _json(await fetch(`${CMP}/runs/${runId}`, { method: 'DELETE' }))
}

/* Pull the rows the application stored, for this run's ticker. Never throws for
   a missing driver or a disabled connection — it answers {ok: false, message}. */
export async function fetchCompareFromDb(runId, body) {
  return _post(`${CMP}/runs/${runId}/fetch`, body)
}

/* The offline route: 1–4 CSV exports in the shape the app's own exports use.
   Each file's table is read from its header row. */
export async function importCompareCsv(runId, files, lane = 'actual') {
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  fd.append('lane', lane)
  fd.append('replace', 'true')
  return _json(await fetch(`${CMP}/runs/${runId}/import`, { method: 'POST', body: fd }))
}

/* Create a run from emails you already have (spec 19.5). Accepts .msg / .eml /
   .html / .txt. The response carries one manifest entry per file with the
   identifiers found in it, so the caller needs no second request. */
export async function uploadCompareEmails(files, label = '', assetClass = 'bonds') {
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  fd.append('label', label)
  fd.append('asset_class', assetClass)
  return _json(await fetch(`${CMP}/uploads`, { method: 'POST', body: fd }))
}

/* The labelling form for one uploaded email: the field list, whatever has been
   stated already, and suggestions read from the email itself (spec 19.6). */
export async function getEmailLabel(runId, emailId, full = false) {
  const q = full ? '?full=true' : ''
  return _json(await fetch(`${CMP}/uploads/${runId}/emails/${emailId}/label${q}`))
}

/* Save it. Replaces this email's expectation — never appends to it. */
export async function putEmailLabel(runId, emailId, label) {
  return _json(await fetch(`${CMP}/uploads/${runId}/emails/${emailId}/label`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label }),
  }))
}

export async function clearEmailLabel(runId, emailId) {
  return _json(await fetch(`${CMP}/uploads/${runId}/emails/${emailId}/label`,
    { method: 'DELETE' }))
}

/* Every label in a run, as a portable document — the sidecar file you didn't have,
   coming back out (spec 19.9). Matches on identifiers, so it restores into a fresh
   store where the row ids differ. */
export async function exportLabels(runId) {
  return _json(await fetch(`${CMP}/uploads/${runId}/labels`))
}

export async function importLabels(runId, document) {
  return _post(`${CMP}/uploads/${runId}/labels/import`, document)
}

/* Score the run. Idempotent — comparing again replaces the previous pass. */
export async function compareRun(runId, body) {
  return _post(`${CMP}/runs/${runId}/compare`, body)
}

export async function getCompareDiff(runId, { table, comparisonId, limit = 20000 } = {}) {
  const q = new URLSearchParams({ limit: String(limit) })
  if (table) q.set('table', table)
  if (comparisonId) q.set('comparison_id', comparisonId)
  return _json(await fetch(`${CMP}/runs/${runId}/diff?${q}`))
}

export async function getCompareDb(envName) {
  const q = envName ? `?env=${encodeURIComponent(envName)}` : ''
  return _json(await fetch(`${CMP}/db${q}`))
}

export async function testCompareDb(envName) {
  return _post(`${CMP}/db/test`, { env: envName })
}

/* A download link. `lane` is 'expected' | 'actual' | 'diff'; a whole-lane CSV
   needs a table because the four have different column sets. */
export function compareExportUrl(runId, lane = 'diff', format = 'csv', table) {
  const q = new URLSearchParams({ format, lane })
  if (table) q.set('table', table)
  return `${CMP}/export/${runId}?${q}`
}
