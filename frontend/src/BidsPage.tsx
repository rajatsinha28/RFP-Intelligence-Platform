import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Download, FileStack, FileUp, Sparkles } from 'lucide-react'
import { api } from './api'
import type { BidDetail, BidItem, BidRecord, ExtractionRun } from './types'

function Badge({ status }: { status: string }) {
  const ok = ['active', 'completed', 'supported'].includes(status)
  return <span className={`status ${ok ? 'ok' : ['processing', 'queued', 'running'].includes(status) ? 'pending' : 'muted'}`}><span />{status.replaceAll('_', ' ')}</span>
}

export function BidsPage({ bids, refresh }: { bids: BidItem[]; refresh: () => Promise<void> }) {
  const [selectedKey, setSelectedKey] = useState('')
  const [detail, setDetail] = useState<BidDetail | null>(null)
  const [record, setRecord] = useState<BidRecord | null>(null)
  const [folder, setFolder] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [run, setRun] = useState<ExtractionRun | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  const loadBid = useCallback(async (bidKey: string) => {
    setSelectedKey(bidKey)
    if (!bidKey) { setDetail(null); setRecord(null); return }
    setDetail(await api.bid(bidKey))
    try { setRecord(await api.bidRecord(bidKey)) } catch { setRecord(null) }
  }, [])

  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status)) return
    const timer = window.setTimeout(async () => {
      const next = await api.extraction(run.id)
      setRun(next)
      if (!['queued', 'running'].includes(next.status)) {
        if (Object.keys(next.output_json).length) setRecord(next.output_json as BidRecord)
        await refresh()
      }
    }, 1800)
    return () => window.clearTimeout(timer)
  }, [refresh, run])

  const importFolder = async () => {
    if (!folder.trim()) return
    setBusy(true); setMessage('')
    try {
      const result = await api.importBidFolder(folder.trim())
      setMessage(`${result.bids.length} bid folders imported; ${result.document_jobs.length} documents queued.`)
      await refresh()
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Import failed') }
    finally { setBusy(false) }
  }

  const upload = async () => {
    if (!selectedKey || !files.length) return
    setBusy(true); setMessage('')
    try {
      const jobs = await api.uploadBidDocuments(selectedKey, files)
      setMessage(`${jobs.length} documents queued for ${selectedKey}.`)
      setFiles([]); await loadBid(selectedKey); await refresh()
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Upload failed') }
    finally { setBusy(false) }
  }

  const extract = async () => {
    if (!selectedKey) return
    setBusy(true); setMessage('')
    try {
      const started = await api.startExtraction(selectedKey)
      setRun(await api.extraction(started.run_id))
      setMessage('Extraction queued. Progress updates automatically.')
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Extraction failed') }
    finally { setBusy(false) }
  }

  const download = () => {
    if (!record) return
    const href = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type: 'application/json' }))
    const anchor = document.createElement('a')
    anchor.href = href; anchor.download = `${selectedKey}.json`; anchor.click()
    URL.revokeObjectURL(href)
  }

  return <div className="stack">
    <section className="split-panels">
      <article className="panel upload-card">
        <div><p className="eyebrow">Assignment data</p><h3>Import bid folders</h3></div>
        <label className="field"><span>Local assignment folder</span><input value={folder} onChange={(event) => setFolder(event.target.value)} placeholder="C:\\...\\Assignment-Data-Statements..." /></label>
        <p>Immediate subfolders become independent bids. Root files, including the assignment PDF, are ignored.</p>
        <button className="primary full" disabled={busy || !folder.trim()} onClick={() => void importFolder()}><FileUp size={17}/>Import folders</button>
      </article>
      <article className="panel upload-card">
        <div><p className="eyebrow">Managed upload</p><h3>Add documents to a bid</h3></div>
        <label className="field"><span>Bid</span><select value={selectedKey} onChange={(event) => void loadBid(event.target.value)}><option value="">Select a bid</option>{bids.map((bid) => <option key={bid.id} value={bid.bid_key}>{bid.bid_key} — {bid.title}</option>)}</select></label>
        <label className="dropzone"><input multiple type="file" onChange={(event) => setFiles(Array.from(event.target.files || []))}/><FileUp size={26}/><strong>{files.length ? `${files.length} files selected` : 'Choose bid documents'}</strong><span>Files are copied to managed storage</span></label>
        <button className="secondary full" disabled={busy || !selectedKey || !files.length} onClick={() => void upload()}>Upload to bid</button>
      </article>
    </section>
    {message && <div className="alert"><CheckCircle2 size={18}/>{message}</div>}
    <section className="panel">
      <div className="panel-heading"><div><p className="eyebrow">Bid registry</p><h3>{detail?.title || 'Bids'}</h3></div><span className="count">{bids.length}</span></div>
      {!bids.length ? <div className="empty"><FileStack size={28}/><strong>No bids imported</strong><span>Import the assignment folder or create a bid through the API.</span></div> : <div className="bid-tabs">{bids.map((bid) => <button className={selectedKey === bid.bid_key ? 'active' : ''} key={bid.id} onClick={() => void loadBid(bid.bid_key)}><strong>{bid.bid_key}</strong><Badge status={bid.status}/></button>)}</div>}
      {detail && <div className="table-wrap"><table><thead><tr><th>Source</th><th>Class</th><th>Addendum</th><th>Status</th></tr></thead><tbody>{detail.documents.map((document) => <tr key={document.id}><td><strong>{document.source_name}</strong><span>{document.relative_path}</span></td><td>{document.doc_type || 'other'}</td><td>{document.addendum_number ?? '—'}</td><td><Badge status={document.status}/></td></tr>)}</tbody></table></div>}
      {detail && <div className="panel-actions"><button className="primary" disabled={busy || !detail.documents.some((item) => item.status === 'active')} onClick={() => void extract()}><Sparkles size={16}/>Extract 20 fields</button>{run && <Badge status={run.status}/>} {record && <button className="secondary" onClick={download}><Download size={16}/>Download JSON</button>}</div>}
    </section>
    {record && <section className="panel">
      <div className="panel-heading"><div><p className="eyebrow">Canonical record</p><h3>Twenty-field extraction</h3></div><Badge status={record.validation.failed ? 'completed_with_issues' : 'completed'}/></div>
      <div className="field-results">{Object.entries(record.fields).map(([name, field]) => <article key={name}><div><strong>{name}</strong><Badge status={field.status}/></div><p>{field.value == null ? field.notes || 'Not found in supplied documents.' : typeof field.value === 'string' ? field.value : JSON.stringify(field.value)}</p><small>{Math.round(field.confidence * 100)}% confidence · {field.sources.length} citation(s)</small>{field.sources.map((source) => <blockquote key={source.chunk_id}><b>{source.file}{source.page ? ` · page ${source.page}` : ''}</b>{source.quote}</blockquote>)}</article>)}</div>
      {record.addendum_changes.length > 0 && <div className="change-log"><h3>Addendum changes</h3>{record.addendum_changes.map((change, index) => <p key={`${change.field}-${index}`}><strong>{change.field}:</strong> {JSON.stringify(change.previous_value)} → {JSON.stringify(change.revised_value)}</p>)}</div>}
    </section>}
  </div>
}
