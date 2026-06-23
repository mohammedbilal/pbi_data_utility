const BASE = '/api'

async function _json(res) {
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text}`)
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
