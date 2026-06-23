import { useEffect, useRef } from 'react'

export default function LogViewer({ logs, running }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  return (
    <div className="log-viewer">
      {logs.length === 0 && !running && (
        <div className="log-empty">No output yet — click Run to start.</div>
      )}
      {logs.map((entry, i) => (
        <div key={i} className={`log-entry log-${entry.level || 'info'}`}>
          <span className="log-time">{entry.time}</span>
          <span className="log-msg">{entry.msg}</span>
        </div>
      ))}
      {running && (
        <div className="log-running-indicator">
          <div className="pulse-dot" />
          running…
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}
