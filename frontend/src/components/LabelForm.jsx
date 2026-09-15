import { useEffect, useMemo, useState } from 'react'
import { getEmailLabel, putEmailLabel, clearEmailLabel } from '../api.js'

/* The labelling form for one uploaded email (spec 19.6).

   An uploaded email has no payload behind it, so its expected values are stated
   here by hand. Three rules drive the whole component:

   * **Blank means the email did not say it** — excluded from scoring, not counted
     as a miss. So an empty box is a legitimate answer and the form never nags for
     one; it says so once, at the top.
   * **A suggestion is not an answer.** Suggestions are read from the email text
     (never from the rows the application stored) and shown as a chip you click to
     accept. Nothing is committed until Save.
   * **Pick, don't type**, wherever the application has a closed vocabulary. */

const GRAIN_TITLE = { deal: 'Deal', tranche: 'Tranche', security: 'Identifier set' }
const FLAVOURS = ['144A', 'Reg S']

const emptyTranche = () => ({ columns: {}, securities: [] })
const emptyDeal = () => ({ deal: {}, tranches: [emptyTranche()] })

export default function LabelForm({ runId, emailId, onSaved }) {
  const [form, setForm] = useState(null)
  const [deals, setDeals] = useState([emptyDeal()])
  const [full, setFull] = useState(false)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState(null)

  useEffect(() => {
    let live = true
    setForm(null); setNote(null)
    getEmailLabel(runId, emailId, full).then(f => {
      if (!live) return
      setForm(f)
      const existing = f.label?.deals
      if (existing?.length) setDeals(existing)
      else setDeals([seedFromSuggestions(f.suggestions)])
    }).catch(e => live && setNote({ kind: 'error', text: String(e.message || e) }))
    return () => { live = false }
  }, [runId, emailId, full])

  const groups = useMemo(
    () => Object.fromEntries((form?.groups || []).map(g => [g.grain, g.fields])), [form])

  if (!form) return <div className="label-form"><p className="empty-note">Loading…</p></div>

  const suggestions = form.suggestions || {}
  const setDeal = (di, next) =>
    setDeals(ds => ds.map((d, i) => (i === di ? { ...d, ...next } : d)))
  const setTranche = (di, ti, next) =>
    setDeals(ds => ds.map((d, i) => i !== di ? d : {
      ...d, tranches: d.tranches.map((t, j) => (j === ti ? { ...t, ...next } : t)),
    }))

  async function save() {
    setBusy('save'); setNote(null)
    try {
      const clean = deals.map(d => ({
        deal: d.deal || {},
        tranches: (d.tranches || []).map(t => ({
          columns: t.columns || {},
          securities: (t.securities || []).filter(s => s.isin || s.cusip || s.figi),
        })),
      }))
      const r = await putEmailLabel(runId, emailId, { deals: clean })
      setNote({
        kind: (r.warnings || []).length ? 'warn' : 'ok',
        text: `Saved — ${r.counts.deals} deal(s), ${r.counts.tranches} tranche(s), `
          + `${r.counts.securities} identifier set(s); `
          + `${r.rendered_fields.length} field(s) will be scored.`
          + ((r.warnings || []).length ? ` ${r.warnings.slice(0, 2).join(' ')}` : ''),
      })
      onSaved?.()
    } catch (e) {
      setNote({ kind: 'error', text: String(e.detail || e.message || e) })
    } finally { setBusy('') }
  }

  async function clear() {
    setBusy('clear')
    try {
      await clearEmailLabel(runId, emailId)
      setDeals([emptyDeal()])
      setNote({ kind: 'ok', text: 'Label cleared.' })
      onSaved?.()
    } catch (e) {
      setNote({ kind: 'error', text: String(e.detail || e.message || e) })
    } finally { setBusy('') }
  }

  return (
    <div className="label-form">
      <p className="label-intro">
        State what the application <b>should</b> have stored for this email. Leave a
        field <b>blank if the email does not say it</b> — a blank is excluded from the
        score rather than counted against the agent, so only fill in what you can
        actually read here.
      </p>

      {(suggestions.notes || []).map((n, i) => (
        <div key={i} className="alert alert-warn">{n}</div>
      ))}
      {note && <div className={`alert alert-${note.kind === 'ok' ? 'success' : note.kind}`}>{note.text}</div>}

      {deals.map((d, di) => (
        <div key={di} className="label-deal">
          <div className="label-deal-head">
            {deals.length > 1 ? `Deal ${di + 1}` : 'Deal'}
            {deals.length > 1 && (
              <button className="btn-link danger"
                onClick={() => setDeals(ds => ds.filter((_, i) => i !== di))}>remove</button>
            )}
          </div>

          <FieldGrid
            fields={groups.deal || []}
            values={d.deal || {}}
            suggested={di === 0 ? suggestions.deal : null}
            onChange={(col, val) => setDeal(di, { deal: { ...d.deal, [col]: val } })}
          />

          {(d.tranches || []).map((t, ti) => (
            <div key={ti} className="label-tranche">
              <div className="label-tranche-head">
                Tranche {ti + 1}
                {(d.tranches.length > 1) && (
                  <button className="btn-link danger"
                    onClick={() => setDeal(di, {
                      tranches: d.tranches.filter((_, i) => i !== ti),
                    })}>remove</button>
                )}
              </div>
              <FieldGrid
                fields={groups.tranche || []}
                values={t.columns || {}}
                suggested={di === 0 ? (suggestions.tranches || [])[ti] : null}
                onChange={(col, val) =>
                  setTranche(di, ti, { columns: { ...t.columns, [col]: val } })}
              />
              <Securities
                fields={groups.security || []}
                rows={t.securities || []}
                identifiers={form.identifiers}
                onChange={rows => setTranche(di, ti, { securities: rows })}
              />
            </div>
          ))}

          <button className="btn-secondary sm"
            onClick={() => setDeal(di, { tranches: [...(d.tranches || []), emptyTranche()] })}>
            + tranche
          </button>
        </div>
      ))}

      <div className="label-actions">
        <button className="btn-secondary sm"
          onClick={() => setDeals(ds => [...ds, emptyDeal()])}>+ deal</button>
        <label className="label-showall">
          <input type="checkbox" checked={full} onChange={e => setFull(e.target.checked)} />
          show every scored field
        </label>
        <span className="spacer" />
        <button className="btn-secondary sm" disabled={!!busy} onClick={clear}>Clear</button>
        <button className="btn-primary sm" disabled={!!busy} onClick={save}>
          {busy === 'save' ? 'Saving…' : 'Save label'}
        </button>
      </div>
    </div>
  )
}

function FieldGrid({ fields, values, suggested, onChange }) {
  return (
    <div className="label-grid">
      {fields.map(f => (
        <Field key={f.column} field={f}
          value={values[f.column]}
          suggestion={suggested ? suggested[f.column] : undefined}
          onChange={v => onChange(f.column, v)} />
      ))}
    </div>
  )
}

function Field({ field, value, suggestion, onChange }) {
  const has = value !== undefined && value !== null && value !== ''
  // Only offer a suggestion for an empty field: overwriting something already
  // stated would put the machine's reading above the human's.
  const offer = !has && suggestion !== undefined && suggestion !== null && suggestion !== ''
  const tooLong = field.max_length && typeof value === 'string'
    && value.length > field.max_length

  return (
    <div className={`label-field${field.matching_only ? ' matching-only' : ''}`}>
      <label title={field.hint || ''}>
        {field.column}
        <span className={`tier t${field.tier}`}>
          {field.matching_only ? 'pairing' : `tier ${field.tier}`}
        </span>
      </label>

      {field.normalizer === 'bool' ? (
        <select value={value === true ? 'true' : value === false ? 'false' : ''}
          onChange={e => onChange(e.target.value === '' ? undefined
            : e.target.value === 'true')}>
          <option value="">— not stated —</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : field.options?.length ? (
        <select value={value ?? ''} onChange={e => onChange(e.target.value || undefined)}>
          <option value="">— not stated —</option>
          {field.options.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : (
        <input type="text" value={value ?? ''} placeholder="— not stated —"
          onChange={e => onChange(e.target.value || undefined)} />
      )}

      {offer && (
        <button className="suggestion" title="read from the email — click to accept"
          onClick={() => onChange(suggestion)}>
          {String(suggestion).slice(0, 42)}
        </button>
      )}
      {tooLong && (
        <span className="field-warn">
          longer than the column ({value.length}/{field.max_length}) — the application
          will truncate or reject it
        </span>
      )}
      {field.hint && <span className="field-hint">{field.hint}</span>}
    </div>
  )
}

/* One row per identifier set. A 144A/Reg S tranche has two, and the Reg S leg may
   carry a CUSIP with no ISIN (D2, confirmed against the application). */
function Securities({ fields, rows, identifiers, onChange }) {
  const spare = (identifiers?.isins || []).filter(
    i => !rows.some(r => r.isin === i))
  const extra = fields.filter(f => !['isin', 'cusip', 'figi'].includes(f.column))

  return (
    <div className="label-securities">
      <div className="label-tranche-head">
        Identifier sets
        <span className="card-note">one per 144A / Reg S leg</span>
      </div>
      {rows.map((r, i) => (
        <div key={i} className="label-security-row">
          <select value={r.flavour || '144A'}
            onChange={e => onChange(rows.map((x, j) =>
              (j === i ? { ...x, flavour: e.target.value } : x)))}>
            {FLAVOURS.map(f => <option key={f} value={f}>{f}</option>)}
          </select>
          {['isin', 'cusip', 'figi'].map(k => (
            <input key={k} type="text" placeholder={k} value={r[k] || ''}
              onChange={e => onChange(rows.map((x, j) =>
                (j === i ? { ...x, [k]: e.target.value || undefined } : x)))} />
          ))}
          {extra.map(f => (
            <select key={f.column} value={r[f.column] || ''}
              title={f.column}
              onChange={e => onChange(rows.map((x, j) =>
                (j === i ? { ...x, [f.column]: e.target.value || undefined } : x)))}>
              <option value="">{f.column}</option>
              {(f.options || []).map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          ))}
          <button className="btn-link danger"
            onClick={() => onChange(rows.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <div className="label-security-add">
        <button className="btn-secondary sm"
          onClick={() => onChange([...rows, { flavour: '144A' }])}>+ identifier set</button>
        {spare.slice(0, 4).map(i => (
          <button key={i} className="suggestion" title="found in this email"
            onClick={() => onChange([...rows, { flavour: '144A', isin: i }])}>{i}</button>
        ))}
      </div>
    </div>
  )
}

/* The first suggestion set becomes the starting point for an unlabelled email —
   the fields are prefilled but nothing is stored until Save. */
function seedFromSuggestions(suggestions) {
  const tranches = (suggestions?.tranches || []).map(t => ({ columns: { ...t }, securities: [] }))
  return {
    deal: { ...(suggestions?.deal || {}) },
    tranches: tranches.length ? tranches : [emptyTranche()],
  }
}
