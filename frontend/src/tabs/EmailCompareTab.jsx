import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  compareRun, deleteCompareRun, fetchCompareFromDb, getCompareDb,
  getCompareDiff, getCompareRun, getCompareRuns, importCompareCsv,
  uploadCompareEmails, compareExportUrl, exportLabels, importLabels,
} from '../api.js'
import LabelForm from '../components/LabelForm.jsx'

/* Email files an upload accepts (spec 19.4). `.pst` is deliberately absent. */
const EMAIL_FILE_RE = /\.(msg|eml|html?|txt)$/i
const isUpload = r => (r?.tool || '').toLowerCase() === 'upload'

const LS_KEY = 'pbi.compare'
const ASSET_CLASSES = [
  { id: 'bonds', label: 'Bonds' },
  { id: 'loans', label: 'Loans' },
  { id: 'abs', label: 'ABS' },
  { id: 'munis', label: 'Munis' },
]
const PROBLEM = new Set(['mismatch', 'missing'])

/* The three questions the headline blends (spec 19.20.1, D17). */
const PROVENANCE_LABEL = [
  ['extracted', 'extracted',
   'Did the agent read this out of the email? The real target — and the only one '
   + 'a prompt change can improve.'],
  ['derived', 'derived by the app',
   'The application computed this from what the agent extracted. A mismatch here '
   + 'is a disagreement between two derivations, not a bad extraction.'],
  ['stamped', 'stamped',
   'A constant the pipeline writes. A plumbing check, not extraction.'],
]

/* One email is one deal, so the diff is grouped that way: deal → tranche →
   fields. Two deliberate choices about which table a field is shown from:

   * `t_issuance_data` (the audit) is the source shown. `t_issuance` holds the
     same 133 values as the current state, so showing both listed every field
     twice — the single biggest reason the flat list was unreadable. It is still
     compared; a disagreement between the two is surfaced as its own note,
     because that means the current-state row does not match its own audit.
   * `t_issuance_deal` fields sit under the deal, securities under the tranche
     they identify. */
function buildTree(rows, problemsOnly) {
  const deals = new Map()

  const deal = seq => {
    if (!deals.has(seq)) {
      deals.set(seq, { seq, fields: [], tranches: new Map(), label: {} })
    }
    return deals.get(seq)
  }
  const tranche = (d, seq) => {
    if (!d.tranches.has(seq)) {
      d.tranches.set(seq, { seq, fields: [], securities: [], divergent: [], label: {} })
    }
    return d.tranches.get(seq)
  }

  // index the current-state rows so we can spot audit-vs-current disagreement
  const current = new Map()
  for (const r of rows) {
    if (r.table === 'issuance') current.set(`${r.util_deal_seq}|${r.util_tranche_seq}|${r.column}`, r)
  }

  for (const r of rows) {
    const d = deal(r.util_deal_seq ?? 1)
    if (r.table === 'issuance_deal') {
      if (r.column === 'issuer_name') d.label.issuer = r.expected_value
      if (r.column === 'deal_currencies') d.label.ccy = r.expected_value
      d.fields.push(r)
    } else if (r.table === 'issuance_data') {
      const t = tranche(d, r.util_tranche_seq ?? 1)
      if (r.column === 'issuer_name') t.label.issuer = r.expected_value
      if (r.column === 'currency_code') t.label.ccy = r.expected_value
      if (r.column === 'tenor') t.label.tenor = r.expected_value
      if (r.column === 'coupon_type') t.label.coupon = r.expected_value
      t.fields.push(r)
      const cur = current.get(`${r.util_deal_seq}|${r.util_tranche_seq}|${r.column}`)
      if (cur && (cur.actual_value ?? null) !== (r.actual_value ?? null)) {
        t.divergent.push({ column: r.column, audit: r.actual_value, current: cur.actual_value })
      }
    } else if (r.table === 'issuance_security') {
      tranche(d, r.util_tranche_seq ?? 1).securities.push(r)
    }
    // `issuance` rows are used for the divergence check only — see above
  }

  /* Only fields the email actually printed can be judged. The rest exist because
     the generator builds a full API payload — it mints a FIGI, a per-flavour
     ticker and more that no template renders — and listing those as "missing"
     made the diff read as a wall of failures the agent could never have avoided.
     They are kept, but behind their own disclosure and never counted. */
  const scoredOf = list => list.filter(f => f.in_email)
  const asideOf = list => list.filter(f => !f.in_email)

  const tally = list => {
    const scored = scoredOf(list)
    return {
      total: scored.length,
      ok: scored.filter(f => !PROBLEM.has(f.verdict)).length,
      bad: scored.filter(f => PROBLEM.has(f.verdict)).length,
      aside: asideOf(list).length,
      // every judgeable field missing means the app never produced this row
      absent: scored.length > 0 && scored.every(f => f.verdict === 'missing'),
    }
  }
  const shown = list => {
    const scored = scoredOf(list)
    return problemsOnly ? scored.filter(f => PROBLEM.has(f.verdict)) : scored
  }

  return [...deals.values()].sort((a, b) => a.seq - b.seq).map(d => {
    const tranches = [...d.tranches.values()].sort((a, b) => a.seq - b.seq).map(t => ({
      ...t,
      stats: tally(t.fields),
      secStats: tally(t.securities),
      shownFields: shown(t.fields),
      shownSecurities: shown(t.securities),
      asideFields: asideOf(t.fields),
      asideSecurities: asideOf(t.securities),
    }))
    const all = [...d.fields, ...tranches.flatMap(t => [...t.fields, ...t.securities])]
    return {
      ...d, tranches,
      stats: tally(d.fields),
      allStats: tally(all),
      shownFields: shown(d.fields),
      asideFields: asideOf(d.fields),
    }
  })
}

function lsRead() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {} } catch { return {} } }
const pct = v => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
const when = ts => (ts ? String(ts).replace('T', ' ').slice(5, 16) : '—')

function DiffRows({ rows, neutral }) {
  return (
    <table className="cmp-diff">
      <thead>
        <tr><th>field</th><th>the email said</th><th>your app stored</th><th /></tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td className="mono">
              {r.column}
              {/* Only the non-extracted ones are marked: labelling every row
                  'extracted' would be noise, and the default reading of a field
                  is that the agent produced it. */}
              {r.provenance && r.provenance !== 'extracted' && (
                <span className={`prov-tag prov-${r.provenance}`}
                  title={r.provenance === 'derived'
                    ? 'The application derives this from what the agent extracted — '
                      + 'a mismatch is a disagreement between two derivations.'
                    : 'A constant the pipeline stamps, not something extracted.'}>
                  {r.provenance}
                </span>
              )}
            </td>
            <td>{r.expected_value ?? <span className="muted">—</span>}</td>
            <td>{r.actual_value ?? <span className="muted">—</span>}</td>
            <td className={neutral ? 'v-aside' : `v-${r.verdict}`}>
              {neutral ? 'not in the email' : (r.verdict === 'match' ? '✓' : r.verdict)}
              {!neutral && r.note && <div className="cmp-note">{r.note}</div>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** The field rows of one scope — deal details, a tranche, or its securities. */
function FieldBlock({ title, stats, rows, aside = [], problemsOnly }) {
  if (!stats.total && !aside.length) return null
  return (
    <div className="cmp-block">
      <div className="cmp-block-head">
        {title}
        <span className="cmp-tally">
          {stats.absent
            ? <em className="v-missing">nothing stored</em>
            : <>{stats.ok}/{stats.total}</>}
        </span>
      </div>
      {rows.length ? <DiffRows rows={rows} /> : (
        <p className="cmp-none">
          {stats.total
            ? (problemsOnly ? 'everything here matched' : 'no fields compared')
            : 'nothing here was mentioned in the email'}
        </p>
      )}
      {!!aside.length && (
        <details className="cmp-aside">
          <summary>
            {aside.length} field(s) the email never mentioned — generated for the
            API payload, so they are not counted
          </summary>
          <DiffRows rows={aside} neutral />
        </details>
      )}
    </div>
  )
}

export default function EmailCompareTab({ activeEnvName }) {
  const ls = lsRead()
  const [assetClass, setAssetClass] = useState(ls.asset_class || 'bonds')
  const [runs, setRuns] = useState([])
  const [store, setStore] = useState(null)
  const [openId, setOpenId] = useState(null)
  const [diff, setDiff] = useState(null)
  const [problemsOnly, setProblemsOnly] = useState(true)
  const [collapsed, setCollapsed] = useState({})
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState(null)
  const [db, setDb] = useState(null)
  const [windowMin, setWindowMin] = useState(60)
  const [dragging, setDragging] = useState(false)
  const fileRef = useRef(null)
  // Uploaded email sets (19.5): the run's detail carries the parsed manifest, and
  // one email at a time is expanded to read its body.
  const [detail, setDetail] = useState(null)
  const [openEmail, setOpenEmail] = useState(null)
  const [uploadDragging, setUploadDragging] = useState(false)
  const uploadRef = useRef(null)

  useEffect(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify({ asset_class: assetClass })) } catch {}
  }, [assetClass])

  const loadRuns = useCallback(async () => {
    try {
      const r = await getCompareRuns(assetClass)
      setRuns(r.runs || [])
      setStore(r.store || null)
    } catch (e) { setMsg({ kind: 'error', text: String(e.message || e) }) }
  }, [assetClass])

  useEffect(() => { loadRuns() }, [loadRuns])

  const openRun = useCallback(async id => {
    setOpenId(id)
    setDiff(null)
    setDetail(null)
    setOpenEmail(null)
    if (!id) return
    // The detail is what carries the uploaded emails and their identifiers; it is
    // cheap and useful for capture runs too, so it is always loaded.
    getCompareRun(id).then(setDetail).catch(() => setDetail(null))
    try {
      const d = await getCompareDiff(id)
      setDiff(d)
    } catch (e) {
      // 404 just means "not compared yet" — not a failure worth shouting about
      if (e.status !== 404) setMsg({ kind: 'error', text: String(e.message || e) })
      setDiff(null)
    }
  }, [])

  const run = useMemo(() => runs.find(r => r.util_run_id === openId) || null, [runs, openId])

  // The fetch queries the database of the environment the email was CAPTURED
  // against, not whatever is selected in the header — an expectation only means
  // anything against the environment it was generated for. So ask about that
  // environment's connection, or the panel would describe a different one.
  useEffect(() => {
    if (!run) { setDb(null); return }
    getCompareDb(run.env || undefined).then(setDb).catch(() => setDb(null))
  }, [run?.util_run_id, run?.env])   // eslint-disable-line react-hooks/exhaustive-deps

  const envMismatch = run?.env && activeEnvName && run.env !== activeEnvName

  async function withBusy(label, fn) {
    setBusy(label); setMsg(null)
    try { await fn() } catch (e) {
      setMsg({ kind: 'error', text: String(e.detail || e.message || e) })
    } finally { setBusy('') }
  }

  /* Labelling is the expensive part, so it is made durable: export writes the whole
     run's labels to a file you can keep, and import restores them into any upload of
     the same emails by matching on identifiers (spec 19.9). */
  const doExportLabels = () => withBusy('labels', async () => {
    const doc = await exportLabels(openId)
    const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `pbi-labels-${(doc.run_label || openId).replace(/[^\w.-]+/g, '-')}.json`
    a.click()
    URL.revokeObjectURL(url)
    setMsg({ kind: 'ok', text: `Exported ${doc.labels.length} label(s). Keep this file — `
      + `re-importing it rebuilds the expectation without reading an email again.` })
  })

  const doImportLabels = file => withBusy('labels', async () => {
    const parsed = JSON.parse(await file.text())
    const r = await importLabels(openId, parsed)
    setMsg({
      kind: r.skipped.length ? 'warn' : 'ok',
      text: `Restored ${r.applied.length} label(s).`
        + (r.skipped.length
          ? ` ${r.skipped.length} could not be placed: `
            + r.skipped.map(s => `${s.file || '?'} (${s.reason})`).slice(0, 3).join('; ')
          : ''),
    })
    await loadRuns(); await openRun(openId)
  })

  const doUpload = files => withBusy('upload', async () => {
    const r = await uploadCompareEmails(files, '', assetClass)
    const unmatched = (r.emails || []).length - (r.matchable || 0)
    setMsg({
      kind: (r.warnings || []).length ? 'warn' : 'ok',
      text: `Uploaded ${(r.emails || []).length} email(s). `
        + `${r.matchable} carry an ISIN or CUSIP`
        + (unmatched ? `; ${unmatched} will need an identifier pinned by hand.` : '.')
        + ((r.warnings || []).length ? ` ${r.warnings.slice(0, 3).join(' ')}` : ''),
    })
    await loadRuns()
    await openRun(r.util_run_id)
  })

  function onUploadDrop(e) {
    e.preventDefault(); setUploadDragging(false)
    const files = [...(e.dataTransfer?.files || [])].filter(f => EMAIL_FILE_RE.test(f.name))
    if (files.length) doUpload(files)
    else setMsg({ kind: 'warn', text: 'Drop .msg, .eml, .html or .txt email files.' })
  }

  const doFetch = () => withBusy('fetch', async () => {
    const r = await fetchCompareFromDb(openId, { window_minutes: Number(windowMin) })
    if (r.ok === false) { setMsg({ kind: 'warn', text: r.message || r.reason }); return }
    const n = Object.values(r.stored || {}).reduce((a, b) => a + b, 0)
    const fetched = Object.values(r.counts?.fetched || {}).reduce((a, b) => a + b, 0)
    // An uploaded set is matched on identifiers, so a ticker-and-window message
    // would describe predicates the query never used (19.8).
    if (r.matched_on === 'identifier') {
      setMsg({
        kind: n === 0 ? 'warn' : 'ok',
        text: n === 0
          ? `No rows in ${r.target} carry any of the ${r.identifiers?.isins || 0} `
            + `ISIN(s) / ${r.identifiers?.cusips || 0} CUSIP(s) from these emails. `
            + `If they have not been through the parsing agent in this environment `
            + `yet, that is the expected answer rather than a fault.`
          : `Loaded ${n} row(s) from ${r.target}, matched on identifier.`,
      })
      await loadRuns(); await openRun(openId)
      return
    }
    if (n === 0) {
      // Nothing found is the common first-run outcome and needs explaining,
      // not a green tick.
      setMsg({
        kind: 'warn',
        text: `No rows for ticker ${r.ticker} in ${r.target}. `
          + (fetched
            ? `${fetched} row(s) carry that ticker but none fall inside the `
              + `${r.window?.minutes}-minute window — widen it above.`
            : `Nothing in that database carries the ticker at all, so the email `
              + `has probably not been parsed into it yet.`)
          + (r.warnings || []).map(w => ` ${w}`).join(''),
      })
    } else {
      setMsg({ kind: 'ok', text: `Loaded ${n} row(s) from ${r.target}.` })
    }
    await loadRuns(); await openRun(openId)
  })

  const doImport = files => withBusy('import', async () => {
    const r = await importCompareCsv(openId, files, 'actual')
    const n = Object.values(r.stored || {}).reduce((a, b) => a + b, 0)
    setMsg({
      kind: (r.warnings || []).length ? 'warn' : 'ok',
      text: `Loaded ${n} row(s) from ${(r.files || []).length} file(s).`
        + ((r.warnings || []).length ? ` ${r.warnings.join(' ')}` : ''),
    })
    await loadRuns(); await openRun(openId)
  })

  const doCompare = () => withBusy('compare', async () => {
    const r = await compareRun(openId)
    setMsg({ kind: 'ok', text: `Compared — ${pct(r.scores?.accuracy)} accuracy.` })
    await loadRuns(); await openRun(openId)
  })

  const doDelete = id => withBusy('delete', async () => {
    await deleteCompareRun(id)
    if (id === openId) { setOpenId(null); setDiff(null) }
    await loadRuns()
  })

  function onDrop(e) {
    e.preventDefault(); setDragging(false)
    const files = [...(e.dataTransfer?.files || [])].filter(f => /\.csv$/i.test(f.name))
    if (files.length) doImport(files)
  }

  /* The manifest written at upload time, joined to the stored email rows. The
     manifest holds the identifiers; the email row holds the body. */
  const uploadEmails = useMemo(() => {
    const manifest = detail?.run?.params?.upload?.emails || []
    if (!manifest.length) return []
    const stored = new Map((detail?.emails || []).map(e => [e.util_email_id, e]))
    return manifest.map(m => ({ ...m, stored: stored.get(m.util_email_id) || null }))
  }, [detail])

  const matchableCount = uploadEmails.filter(e => e.matchable).length
  const unlabelledCount = uploadEmails.filter(
    e => !(e.stored?.rendered_fields || []).length).length
  const openEmailBody = useMemo(
    () => uploadEmails.find(e => e.util_email_id === openEmail)?.stored || null,
    [uploadEmails, openEmail])

  const tree = useMemo(
    () => buildTree(diff?.rows || [], problemsOnly), [diff, problemsOnly])
  const s = diff?.scores

  const toggle = key => setCollapsed(c => ({ ...c, [key]: !c[key] }))
  const isOpen = (key, dflt) => (key in collapsed ? !collapsed[key] : dflt)

  return (
    <div className="compare-view">
      <div className="segmented">
        {ASSET_CLASSES.map(a => (
          <button key={a.id}
            className={`seg-btn${assetClass === a.id ? ' active' : ''}`}
            disabled={a.id !== 'bonds'}
            title={a.id === 'bonds' ? '' : 'not yet available'}
            onClick={() => setAssetClass(a.id)}>{a.label}</button>
        ))}
      </div>

      {msg && <div className={`alert alert-${msg.kind === 'ok' ? 'success' : msg.kind}`}>{msg.text}</div>}

      {/* ── 1 · runs ───────────────────────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          Email runs
          <span className="card-note">
            {store?.exists ? `${runs.length} run(s)` : 'no runs yet'}
          </span>
        </div>
        <div className="card-body">
          {/* Uploading an existing email set (19.5). A generated capture run comes
              from the Bonds tab; this is the other way in. */}
          <div className={`drop-zone${uploadDragging ? ' drag-over' : ''}`}
            onDragOver={e => { e.preventDefault(); setUploadDragging(true) }}
            onDragLeave={() => setUploadDragging(false)}
            onDrop={onUploadDrop}
            onClick={() => uploadRef.current?.click()}>
            <input ref={uploadRef} type="file" multiple hidden
              accept=".msg,.eml,.html,.htm,.txt"
              onChange={e => {
                const files = [...(e.target.files || [])]
                e.target.value = ''
                if (files.length) doUpload(files)
              }} />
            {busy === 'upload'
              ? 'Reading emails…'
              : <>Drop <b>.msg</b> emails here to test against a set you already have,
                  or click to choose. Nothing is sent anywhere — the files are parsed
                  locally.</>}
          </div>

          {!runs.length && (
            <p className="empty-note">
              No runs yet. Either upload emails above, or turn on <b>Email</b> and
              <b>&nbsp;Capture expectations</b> on the Bonds tab and run — that saves
              what each generated email said, ready to compare against the
              application database.
            </p>
          )}
          {!!runs.length && (
            <table className="cmp-runs">
              <thead>
                <tr><th>when</th><th>environment</th><th>format</th><th>deals</th>
                  <th>ticker</th><th>app rows</th>
                  <th className="num">accuracy</th><th /></tr>
              </thead>
              <tbody>
                {runs.map(r => (
                  <tr key={r.util_run_id}
                    className={r.util_run_id === openId ? 'open' : ''}
                    onClick={() => openRun(r.util_run_id === openId ? null : r.util_run_id)}>
                    <td>{when(r.ts)}</td>
                    <td className={r.env === activeEnvName ? '' : 'muted'}
                      title={r.env === activeEnvName ? '' :
                        'captured against a different environment than the one selected now'}>
                      {r.env || '—'}
                    </td>
                    <td className="mono">
                      {isUpload(r)
                        ? <span title={r.notes || 'uploaded email set'}>uploaded</span>
                        : (r.email_format || '—')}
                    </td>
                    <td>
                      {isUpload(r)
                        // An upload knows how many emails it has, not how many deals
                        // they describe — that is a labelling input (19.17).
                        ? <span className="muted">{r.emails || 0} emails</span>
                        : <>{r.deals || 0} / {r.tranches || 0}</>}
                    </td>
                    <td className="mono">{r.ticker || '—'}</td>
                    <td>{r.has_actual ? `${r.rows?.actual || 0} loaded` : <span className="muted">not loaded</span>}</td>
                    <td className="num">{pct(r.latest_comparison?.scores?.accuracy)}</td>
                    <td className="row-actions">
                      <button className="btn-link danger" title="delete this run"
                        onClick={e => { e.stopPropagation(); doDelete(r.util_run_id) }}>✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {run && isUpload(run) && (
        /* ── 1b · the uploaded emails ─────────────────────────────────
           Only for an upload run: a capture run's emails were generated from a
           payload the utility already holds, so there is nothing to inspect. */
        <div className="card">
          <div className="card-header">
            Uploaded emails
            <span className="card-note">
              {uploadEmails.length} email(s) · {matchableCount} with an identifier
              {unlabelledCount ? ` · ${unlabelledCount} not yet labelled` : ''}
            </span>
          </div>
          <div className="card-body">
            <p className="empty-note" style={{ marginTop: 0 }}>
              Click an email to read it and state what your application should have
              stored for it. Leave anything the email doesn't say <b>blank</b> — blanks
              are excluded from the score rather than counted against the agent.
            </p>

            <div className="label-actions" style={{ marginTop: 0, paddingTop: 0, borderTop: 0 }}>
              <button className="btn-secondary sm"
                disabled={busy === 'labels' || unlabelledCount === uploadEmails.length}
                title={unlabelledCount === uploadEmails.length
                  ? 'nothing is labelled yet' : 'save the labels to a file'}
                onClick={doExportLabels}>Export labels</button>
              <label className="btn-secondary sm" style={{ cursor: 'pointer' }}>
                Import labels
                <input type="file" accept=".json,application/json" hidden
                  onChange={e => {
                    const f = e.target.files?.[0]
                    e.target.value = ''
                    if (f) doImportLabels(f)
                  }} />
              </label>
              <span className="card-note">
                labelling once is the cost; the export is how you never pay it twice
              </span>
            </div>
            <table className="cmp-runs">
              <thead>
                <tr><th>#</th><th>subject</th><th>sent</th><th>ticker</th>
                  <th>identifiers</th><th>labelled</th><th /></tr>
              </thead>
              <tbody>
                {uploadEmails.map(e => {
                  const ids = e.identifiers || {}
                  const nIsin = (ids.isins || []).length
                  const nCusip = (ids.cusips || []).length
                  const open = openEmail === e.util_email_id
                  return (
                    <tr key={e.util_email_id || e.seq}
                      className={open ? 'open' : ''}
                      onClick={() => setOpenEmail(open ? null : e.util_email_id)}>
                      <td>{e.seq}</td>
                      <td title={e.file}>{e.subject || e.file || '—'}</td>
                      <td>{e.sent_at ? when(e.sent_at) : <span className="muted">—</span>}</td>
                      <td className="mono">{(ids.tickers || [])[0] || <span className="muted">—</span>}</td>
                      <td className="mono">
                        {nIsin || nCusip
                          ? `${nIsin} ISIN · ${nCusip} CUSIP`
                          : <span className="warn-text" title="cannot be paired with
                              application rows until an identifier is pinned">
                              none — needs a manual pin</span>}
                      </td>
                      <td>{(e.stored?.rendered_fields || []).length
                        ? <span className="v-match">{e.stored.rendered_fields.length} fields</span>
                        : <span className="muted">no</span>}</td>
                      <td className="row-actions">{open ? '▾' : '▸'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            {openEmailBody && (
              /* Email on the left, form on the right (19.6) — you are reading one
                 and filling in the other, so they have to be side by side. */
              <div className="label-split">
                <div className="email-preview">
                  <div className="email-preview-head mono">
                    {openEmailBody.subject || '(no subject)'}
                    {openEmailBody.send_note ? ` · ${openEmailBody.send_note}` : ''}
                  </div>
                  {/* Sandboxed with no allow-scripts, and srcDoc rather than
                      dangerouslySetInnerHTML: this is third-party HTML from a real
                      inbox and it never gets to run anything (19.4). */}
                  <iframe title="email" sandbox="" className="email-frame"
                    srcDoc={openEmailBody.body_html || ''} />
                </div>
                <LabelForm runId={openId} emailId={openEmail}
                  onSaved={async () => { await loadRuns(); await openRun(openId) }} />
              </div>
            )}
          </div>
        </div>
      )}

      {run && (
        <>
          {/* ── 2 · load the application's rows ──────────────────────── */}
          <div className="card">
            <div className="card-header">
              Load the rows your application stored
              <span className="card-note mono">{run.env} · {run.ticker}</span>
            </div>
            <div className="card-body cmp-source">
              {envMismatch && (
                <div className="alert alert-warn" style={{ gridColumn: '1 / -1' }}>
                  This run was captured against <b>{run.env}</b>, so its rows are read
                  from that environment's database — not <b>{activeEnvName}</b>, which
                  is selected in the header. An expectation only means anything against
                  the environment its email was generated for.
                </div>
              )}
              <div>
                <div className="field">
                  <label>Look for rows written within (minutes)</label>
                  <input type="number" min="1" value={windowMin}
                    onChange={e => setWindowMin(e.target.value)} />
                  <div className="field-hint">
                    {db?.available
                      ? `read-only query on ${db.target}`
                      : (db?.reason || 'database not configured')}
                  </div>
                </div>
                <button className="btn btn-primary" disabled={!db?.available || !!busy}
                  onClick={doFetch}>
                  {busy === 'fetch' ? 'Querying…' : 'Fetch from database'}
                </button>
              </div>

              <div className={`drop-zone${dragging ? ' drag-over' : ''}`}
                onDragOver={e => { e.preventDefault(); setDragging(true) }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                onClick={() => fileRef.current?.click()}>
                <div className="drop-zone-icon">📂</div>
                <div className="drop-zone-label">
                  …or drop the CSV exports of your four tables
                </div>
                <div className="field-hint">the table is read from each file's header</div>
                <input ref={fileRef} type="file" accept=".csv" multiple hidden
                  onChange={e => { const f = [...e.target.files]; e.target.value = ''; if (f.length) doImport(f) }} />
              </div>
            </div>
          </div>

          {/* ── 3 · result ───────────────────────────────────────────── */}
          <div className="card">
            <div className="card-header">
              Result
              <span className="card-note">
                {diff ? `compared ${when(diff.ts)}` : 'not compared yet'}
                <button className="btn btn-small" disabled={!run.has_actual || !!busy}
                  onClick={doCompare}>
                  {busy === 'compare' ? 'Comparing…' : diff ? 'Compare again' : 'Compare'}
                </button>
              </span>
            </div>
            <div className="card-body">
              {!run.has_actual && (
                <p className="empty-note">
                  Load the application's rows above, then press Compare.
                </p>
              )}

              {s && (
                <>
                  <div className="cmp-score">
                    <div className="cmp-score-value">{pct(s.accuracy)}</div>
                    <div className="cmp-score-line">
                      {s.fields_matched} of {s.fields_compared} fields matched
                      {' · '}{s.rows_matched} row(s) matched
                      {s.rows_missing ? ` · ${s.rows_missing} row(s) the app never created` : ''}
                      {s.rows_unexpected ? ` · ${s.rows_unexpected} extra row(s)` : ''}
                    </div>
                    {!!s.fields_not_in_email && (
                      <div className="cmp-score-note">
                        {s.fields_not_in_email} field(s) weren't mentioned in the email,
                        so they aren't counted
                      </div>
                    )}
                    {!!s.fields_in_unmatched_rows && (
                      /* Excluded on purpose: every field of an unpaired row is
                         trivially missing, so counting them measures coverage rather
                         than accuracy (spec 18.7). Shown so the exclusion is never
                         silent — this is the difference between "the agent is bad"
                         and "the agent never saw these emails". */
                      <div className="cmp-score-note">
                        {s.fields_in_unmatched_rows} field(s) belong to {s.rows_missing}{' '}
                        row(s) your application never created, so they aren't scored as
                        wrong answers — that's a coverage gap, not an accuracy problem
                      </div>
                    )}

                    {/* D17 — the headline blends three different questions, and only
                        the first is one a prompt change can move. Showing them apart
                        stops a derived mismatch sending someone to edit a prompt that
                        was never involved. */}
                    {s.by_provenance && (
                      <div className="cmp-provenance">
                        {PROVENANCE_LABEL.map(([key, title, why]) => {
                          const b = s.by_provenance[key]
                          if (!b || !b.compared) return null
                          return (
                            <div key={key} className="cmp-prov" title={why}>
                              <span className="cmp-prov-name">{title}</span>
                              <span className="cmp-prov-value">{pct(b.accuracy)}</span>
                              <span className="cmp-prov-count">
                                {b.matched}/{b.compared}
                              </span>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>

                  {!s.rows_matched && (
                    <div className="alert alert-warn">
                      Nothing matched. The email format must print the issuer ticker or a
                      security identifier for its rows to be findable — check the format
                      rather than reading this as a low score.
                    </div>
                  )}

                  <div className="cmp-tabs">
                    <span className="cmp-legend">
                      one email = one deal · click a heading to open it
                    </span>
                    <label className="cmp-filter">
                      <input type="checkbox" checked={problemsOnly}
                        onChange={e => setProblemsOnly(e.target.checked)} />
                      problems only
                    </label>
                    <a className="btn-link" href={compareExportUrl(openId, 'diff', 'csv')}>
                      Export CSV
                    </a>
                  </div>

                  {tree.map(d => {
                    const key = `d${d.seq}`
                    const open = isOpen(key, d.allStats.bad > 0)
                    return (
                      <div key={key} className={`cmp-deal${d.allStats.bad ? '' : ' clean'}`}>
                        <button className="cmp-deal-head" onClick={() => toggle(key)}>
                          <span className="cmp-caret">{open ? '▾' : '▸'}</span>
                          <b>Deal {d.seq}</b>
                          <span className="cmp-deal-name">
                            {d.label.issuer || '—'}
                            {d.label.ccy ? ` · ${d.label.ccy}` : ''}
                          </span>
                          <span className="cmp-tally">
                            {/* "absent" must mean the WHOLE deal is missing. A
                                deal whose tranches arrived but whose deal-level
                                row did not is a different finding, called out
                                inside. */}
                            {d.allStats.absent
                              ? <em className="v-missing">the app never created this deal</em>
                              : <>{d.allStats.ok}/{d.allStats.total} fields
                                {d.allStats.bad
                                  ? <b className="v-mismatch"> · {d.allStats.bad} wrong</b>
                                  : <b className="v-match"> ✓</b>}</>}
                          </span>
                        </button>

                        {open && (
                          <div className="cmp-deal-body">
                            {d.stats.absent && !d.allStats.absent && (
                              <div className="alert alert-warn">
                                The tranches below arrived, but no deal-level row was
                                created for this deal. If this run pre-dates per-deal
                                tickers, its deals share one ticker and only the first
                                can be matched at deal level — re-run to get a clean read.
                              </div>
                            )}
                            <FieldBlock title="deal details" stats={d.stats} rows={d.shownFields}
                              aside={d.asideFields} problemsOnly={problemsOnly} />

                            {d.tranches.map(t => {
                              const tk = `${key}t${t.seq}`
                              const topen = isOpen(tk, t.stats.bad + t.secStats.bad > 0)
                              return (
                                <div key={tk} className="cmp-tranche">
                                  <button className="cmp-tranche-head" onClick={() => toggle(tk)}>
                                    <span className="cmp-caret">{topen ? '▾' : '▸'}</span>
                                    Tranche {t.seq}
                                    <span className="cmp-deal-name">
                                      {[t.label.tenor, t.label.ccy, t.label.coupon]
                                        .filter(Boolean).join(' · ') || '—'}
                                    </span>
                                    <span className="cmp-tally">
                                      {t.stats.absent
                                        ? <em className="v-missing">not created by the app</em>
                                        : <>{t.stats.ok}/{t.stats.total}
                                          {t.stats.bad
                                            ? <b className="v-mismatch"> · {t.stats.bad} wrong</b>
                                            : <b className="v-match"> ✓</b>}</>}
                                    </span>
                                  </button>
                                  {topen && (
                                    <div className="cmp-tranche-body">
                                      <FieldBlock title="tranche" stats={t.stats}
                                        rows={t.shownFields} aside={t.asideFields}
                                        problemsOnly={problemsOnly} />
                                      {!!t.securities.length && (
                                        <FieldBlock title="security identifiers"
                                          stats={t.secStats} rows={t.shownSecurities}
                                          aside={t.asideSecurities}
                                          problemsOnly={problemsOnly} />
                                      )}
                                      {!!t.divergent.length && (
                                        <div className="alert alert-warn">
                                          The current-state row disagrees with its own audit
                                          row on {t.divergent.map(x => x.column).join(', ')} —
                                          that is an application inconsistency, not an
                                          extraction error.
                                        </div>
                                      )}
                                    </div>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )
                  })}

                  {!tree.length && (
                    <p className="empty-note">Nothing to show for this run.</p>
                  )}
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
