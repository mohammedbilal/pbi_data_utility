export default function Header({ envNames, activeEnvName, activeEnvHost, onSwitchEnv }) {
  return (
    <header className="header">
      <div className="header-logo">P</div>
      <div className="header-title-block">
        <span className="header-title">PBI Test Utility</span>
        <span className="header-subtitle">data insertion console</span>
      </div>

      <div className="header-spacer" />

      <span className="header-env-label">env</span>
      <select
        className="env-select"
        value={activeEnvName}
        onChange={e => onSwitchEnv(e.target.value)}
      >
        {envNames.map(name => (
          <option key={name} value={name}>{name}</option>
        ))}
      </select>
      {activeEnvHost && (
        <span className="env-badge" title={activeEnvHost}>{activeEnvHost}</span>
      )}
    </header>
  )
}
