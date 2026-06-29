import { useRef, useState } from 'react'
import { createEventSource, startRun, stopRun } from '../api.js'

export function useRunState(tool, onFinish, startFn) {
  const [running, setRunning] = useState(false)
  const [logs, setLogs] = useState([])
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState(null)
  const [runId, setRunId] = useState(null)
  const [status, setStatus] = useState('idle') // idle | running | done | failed | stopped

  const esRef = useRef(null)
  const startRef = useRef(0)
  const summaryRef = useRef(null)
  const stoppedRef = useRef(false)
  const finishedRef = useRef(true)

  function now() {
    return new Date().toLocaleTimeString('en-GB', { hour12: false })
  }

  // Compute final status + duration and hand the record core to onFinish.
  // Guarded so a normal `done` after a user `stop` can't double-record.
  function finish() {
    if (finishedRef.current) return
    finishedRef.current = true

    const sum = summaryRef.current || { success: 0, failed: 0, total: 0 }
    const durSeconds = (Date.now() - startRef.current) / 1000
    const finalStatus = stoppedRef.current
      ? 'stopped'
      : (sum.failed > 0 ? 'failed' : 'done')

    setStatus(finalStatus)
    setRunning(false)

    if (onFinish) {
      onFinish({
        ok: sum.success,
        fail: sum.failed,
        total: sum.total,
        status: finalStatus,
        durSeconds,
      })
    }
  }

  async function start(params) {
    setLogs([])
    setSummary(null)
    setError(null)
    summaryRef.current = null
    stoppedRef.current = false
    finishedRef.current = false
    startRef.current = Date.now()

    let data
    try {
      data = await (startFn ? startFn(params) : startRun(tool, params))
    } catch (err) {
      setError(err.message)
      finishedRef.current = true
      return
    }

    setRunId(data.run_id)
    setRunning(true)
    setStatus('running')

    const es = createEventSource(tool, data.run_id)
    esRef.current = es

    es.onmessage = (e) => {
      let msg
      try { msg = JSON.parse(e.data) } catch { return }

      if (msg.type === 'done') {
        es.close()
        finish()
      } else if (msg.type === 'log') {
        setLogs(prev => [...prev, { ...msg, time: now() }])
      } else if (msg.type === 'summary') {
        summaryRef.current = msg
        setSummary(msg)
      }
      // ignore ping
    }

    es.onerror = () => {
      es.close()
      finish()
    }
  }

  async function stop() {
    if (!runId) return
    stoppedRef.current = true
    try { await stopRun(tool, runId) } catch { /* ignore */ }
    esRef.current?.close()
    finish()
  }

  return { running, logs, summary, error, runId, status, start, stop }
}
