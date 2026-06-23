import LogViewer from './LogViewer.jsx'

const STATUS_TEXT = {
  idle: 'idle',
  running: 'running',
  done: 'done',
  stopped: 'stopped',
}

export default function LiveOutput({ logs, running, summary, runId, status }) {
  const shortId = runId ? runId.replace(/-/g, '').slice(0, 4) : ''
  const statusText = status === 'failed'
    ? `done · ${summary?.failed ?? 0} failed`
    : (STATUS_TEXT[status] || 'idle')

  return (
    <div className="card">
      <div className="live-header">
        <span className="live-title">Live output</span>
        {shortId && <span className="live-runid">run {shortId}</span>}
        <div className="live-header-spacer" />
        <span className={`status-chip status-${status}`}>
          <span className="status-dot" />
          {statusText}
        </span>
      </div>

      {summary && (
        <div className="live-stats">
          <div className="stat stat-success">
            <div className="stat-value">{summary.success}</div>
            <div className="stat-label">success</div>
          </div>
          <div className="stat stat-failed">
            <div className="stat-value">{summary.failed}</div>
            <div className="stat-label">failed</div>
          </div>
          <div className="stat stat-total">
            <div className="stat-value">{summary.total}</div>
            <div className="stat-label">total</div>
          </div>
        </div>
      )}

      <LogViewer logs={logs} running={running} />
    </div>
  )
}
