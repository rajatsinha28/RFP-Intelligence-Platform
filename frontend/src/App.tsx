import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  Activity,
  BarChart3,
  BookOpenText,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleGauge,
  Database,
  FileStack,
  FileUp,
  Gauge,
  KeyRound,
  Link2,
  LoaderCircle,
  MessageSquareText,
  RefreshCw,
  Search,
  Send,
  ServerCog,
  ShieldCheck,
  Sparkles,
  Trash2,
  XCircle,
} from 'lucide-react'
import { api, getStoredApiKey, setStoredApiKey, streamQuery } from './api'
import { BidsPage } from './BidsPage'
import type { BidItem, Citation, DocumentItem, Health, QueryResponse, View } from './types'

const navigation: Array<{ id: View; label: string; icon: typeof Gauge }> = [
  { id: 'overview', label: 'Overview', icon: Gauge },
  { id: 'bids', label: 'RFP bids', icon: FileStack },
  { id: 'documents', label: 'Documents', icon: FileStack },
  { id: 'chat', label: 'Grounded chat', icon: MessageSquareText },
  { id: 'evaluation', label: 'Evaluation', icon: BarChart3 },
  { id: 'system', label: 'System health', icon: ServerCog },
]

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

function StatusBadge({ status }: { status: string }) {
  const ok = ['active', 'completed', 'ready', 'ok', 'configured', 'answered'].includes(status)
  return <span className={`status ${ok ? 'ok' : status === 'processing' ? 'pending' : 'muted'}`}><span />{status.replace('_', ' ')}</span>
}

function App() {
  const [view, setView] = useState<View>('overview')
  const [health, setHealth] = useState<Health | null>(null)
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [rfpDocuments, setRfpDocuments] = useState<DocumentItem[]>([])
  const [bids, setBids] = useState<BidItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [apiKeyDraft, setApiKeyDraft] = useState(getStoredApiKey)
  const [keyConfigured, setKeyConfigured] = useState(() => Boolean(getStoredApiKey()))

  const refresh = useCallback(async () => {
    setLoading(true)
    const [healthResult, documentsResult, rfpDocumentsResult, bidsResult] = await Promise.allSettled([api.health(), api.documents(), api.documents('rfp-intelligence'), api.bids()])
    if (healthResult.status === 'fulfilled') setHealth(healthResult.value)
    if (documentsResult.status === 'fulfilled') setDocuments(documentsResult.value.items)
    if (rfpDocumentsResult.status === 'fulfilled') setRfpDocuments(rfpDocumentsResult.value.items)
    if (bidsResult.status === 'fulfilled') setBids(bidsResult.value.items)
    if (documentsResult.status === 'rejected') {
      const message = documentsResult.reason instanceof Error ? documentsResult.reason.message : ''
      setError(message.includes('Invalid API key') ? 'Enter the APP_API_KEY from your .env file in the sidebar, then select Connect.' : 'The document API is not reachable yet. Check the API logs and try again.')
    } else if (healthResult.status === 'rejected') {
      setError('The local API is not reachable yet. Start Docker Compose and try again.')
    } else {
      setError('')
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0)
    return () => window.clearTimeout(timer)
  }, [refresh])

  const activeDocuments = documents.filter((document) => document.status === 'active').length
  const activeRfpDocuments = rfpDocuments.filter((document) => document.status === 'active').length
  const latestUpdate = documents[0]?.updated_at
  const saveApiKey = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const value = apiKeyDraft.trim()
    setStoredApiKey(value)
    setKeyConfigured(Boolean(value))
    void refresh()
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Sparkles size={20} /></div><div><strong>Advance RAG</strong><span>Grounded intelligence</span></div></div>
        <nav>
          <p className="nav-label">Workspace</p>
          {navigation.map((item) => {
            const Icon = item.icon
            return <button key={item.id} className={view === item.id ? 'active' : ''} onClick={() => setView(item.id)}><Icon size={18} /><span>{item.label}</span>{view === item.id && <ChevronRight size={15} />}</button>
          })}
        </nav>
        <div className="sidebar-foot">
          <form className="api-key-box" onSubmit={saveApiKey}>
            <label htmlFor="app-api-key"><span><KeyRound size={13}/>Local API key</span></label>
            <input id="app-api-key" type="password" autoComplete="off" value={apiKeyDraft} onChange={(event) => setApiKeyDraft(event.target.value)} placeholder="Paste APP_API_KEY" />
            <button type="submit">{keyConfigured ? 'Update key' : 'Connect'}</button>
            <small>{keyConfigured ? 'Stored for this browser session' : 'Required when APP_API_KEY is set'}</small>
          </form>
          <div className="mini-health"><span className={health?.status === 'ready' ? 'pulse good' : 'pulse'} /><div><strong>{health?.status === 'ready' ? 'All systems ready' : 'Setup required'}</strong><span>Local environment</span></div></div>
          <a href="/docs" target="_blank" rel="noreferrer">Open API docs <ChevronRight size={14} /></a>
        </div>
      </aside>

      <main>
        <header>
          <div><p className="eyebrow">Knowledge workspace</p><h1>{navigation.find((item) => item.id === view)?.label}</h1></div>
          <button className="icon-button" onClick={() => void refresh()} aria-label="Refresh"><RefreshCw size={18} className={loading ? 'spin' : ''} /></button>
        </header>
        {error && <div className="alert error"><XCircle size={18} />{error}</div>}
        {view === 'overview' && <Overview health={health} documents={documents} activeDocuments={activeDocuments} latestUpdate={latestUpdate} setView={setView} />}
        {view === 'bids' && <BidsPage bids={bids} refresh={refresh} />}
        {view === 'documents' && <Documents documents={documents} refresh={refresh} />}
        {view === 'chat' && <Chat disabled={(bids.length > 0 ? activeRfpDocuments : activeDocuments) === 0} bids={bids} />}
        {view === 'evaluation' && <Evaluation />}
        {view === 'system' && <SystemHealth health={health} />}
      </main>
    </div>
  )
}

function Overview({ health, documents, activeDocuments, latestUpdate, setView }: { health: Health | null; documents: DocumentItem[]; activeDocuments: number; latestUpdate?: string; setView: (view: View) => void }) {
  const cards = [
    { label: 'Indexed documents', value: activeDocuments, note: `${documents.length} total records`, icon: BookOpenText },
    { label: 'Knowledge status', value: health?.status === 'ready' ? 'Ready' : 'Needs setup', note: 'OpenAI · Pinecone · Redis', icon: ShieldCheck },
    { label: 'Retrieval mode', value: 'Hybrid', note: 'Dense + BM25 + reranking', icon: Search },
    { label: 'Last update', value: latestUpdate ? new Date(latestUpdate).toLocaleDateString() : '—', note: 'Knowledge-base versioned', icon: Activity },
  ]
  return <div className="content-grid">
    <section className="hero"><div><span className="kicker"><Bot size={15} /> Evidence-first answers</span><h2>Your documents, answered with proof.</h2><p>Ingest mixed-format sources, retrieve with dense and keyword signals, and generate answers only when the evidence is strong enough.</p><div className="hero-actions"><button className="primary" onClick={() => setView('documents')}><FileUp size={17} />Add knowledge</button><button className="secondary" onClick={() => setView('chat')}><MessageSquareText size={17} />Ask a question</button></div></div><div className="orb"><div className="orbit one"/><div className="orbit two"/><Sparkles size={36}/></div></section>
    <section className="stat-grid">{cards.map((card) => { const Icon = card.icon; return <article className="stat-card" key={card.label}><div className="stat-icon"><Icon size={19}/></div><span>{card.label}</span><strong>{card.value}</strong><small>{card.note}</small></article> })}</section>
    <section className="panel pipeline"><div className="panel-heading"><div><p className="eyebrow">Live architecture</p><h3>Adaptive retrieval pipeline</h3></div><StatusBadge status={health?.status || 'offline'} /></div><div className="pipeline-row">{['Cache', 'Route', 'Dense + BM25', 'Rerank', 'Ground', 'Cite'].map((step, index) => <div className="pipeline-step" key={step}><span>{index + 1}</span><strong>{step}</strong>{index < 5 && <ChevronRight size={16}/>}</div>)}</div></section>
  </div>
}

function Documents({ documents, refresh }: { documents: DocumentItem[]; refresh: () => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const upload = async () => { if (!file) return; setBusy(true); setMessage(''); try { await api.upload(file); setMessage('Upload queued. The worker is parsing and indexing it.'); setFile(null); setTimeout(() => void refresh(), 1200) } catch (err) { setMessage(err instanceof Error ? err.message : 'Upload failed') } finally { setBusy(false) } }
  const ingest = async () => { if (!url) return; setBusy(true); setMessage(''); try { await api.ingestUrl(url); setMessage('Web ingestion queued.'); setUrl(''); setTimeout(() => void refresh(), 1200) } catch (err) { setMessage(err instanceof Error ? err.message : 'Ingestion failed') } finally { setBusy(false) } }
  const remove = async (id: string) => { if (!window.confirm('Delete this document from the knowledge base?')) return; await api.deleteDocument(id); await refresh() }
  return <div className="stack">
    <section className="split-panels"><article className="panel upload-card"><div className="panel-heading"><div className="stat-icon"><FileUp size={19}/></div><div><h3>Upload a document</h3><p>PDF, DOCX, CSV, JSON, HTML, text, or image</p></div></div><label className="dropzone"><input type="file" onChange={(event) => setFile(event.target.files?.[0] || null)} /><FileUp size={28}/><strong>{file ? file.name : 'Choose a file'}</strong><span>{file ? formatBytes(file.size) : 'Maximum 50 MB'}</span></label><button className="primary full" disabled={!file || busy} onClick={() => void upload()}>{busy ? <LoaderCircle className="spin" size={17}/> : <FileUp size={17}/>}Upload and index</button></article>
      <article className="panel upload-card"><div className="panel-heading"><div className="stat-icon"><Link2 size={19}/></div><div><h3>Ingest a web page</h3><p>Public HTTP or HTTPS source</p></div></div><label className="field"><span>Page URL</span><input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/policy" /></label><div className="safe-note"><ShieldCheck size={17}/><span>Private networks, unsafe redirects, and oversized responses are blocked.</span></div><button className="secondary full" disabled={!url || busy} onClick={() => void ingest()}><Link2 size={17}/>Fetch and index</button></article></section>
    {message && <div className="alert"><CheckCircle2 size={18}/>{message}</div>}
    <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Knowledge base</p><h3>Documents</h3></div><span className="count">{documents.length}</span></div>{documents.length === 0 ? <Empty icon={Database} title="No documents indexed" text="Upload your first source to begin grounded retrieval."/> : <div className="table-wrap"><table><thead><tr><th>Document</th><th>Type</th><th>Version</th><th>Size</th><th>Status</th><th/></tr></thead><tbody>{documents.map((document) => <tr key={document.id}><td><strong>{document.title}</strong><span>{document.source_name}</span></td><td>{document.source_type.toUpperCase()}</td><td>v{document.current_version}</td><td>{formatBytes(document.size_bytes)}</td><td><StatusBadge status={document.status}/></td><td><button className="danger-icon" onClick={() => void remove(document.id)} aria-label={`Delete ${document.title}`}><Trash2 size={17}/></button></td></tr>)}</tbody></table></div>}</section>
  </div>
}

function Chat({ disabled, bids }: { disabled: boolean; bids: BidItem[] }) {
  const [query, setQuery] = useState('')
  const [answer, setAnswer] = useState('')
  const [citations, setCitations] = useState<Citation[]>([])
  const [result, setResult] = useState<QueryResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [bidId, setBidId] = useState('')
  const ask = async () => { if (!query.trim() || busy) return; setBusy(true); setAnswer(''); setCitations([]); setResult(null); setError(''); try { const response = await streamQuery(query, (text) => setAnswer((current) => current + text), (citation) => setCitations((current) => current.some((item) => item.chunk_id === citation.chunk_id) ? current : [...current, citation]), bids.length ? 'rfp-intelligence' : 'default', bidId ? { bid_id: bidId } : {}); setResult(response) } catch (err) { setError(err instanceof Error ? err.message : 'Query failed') } finally { setBusy(false) } }
  return <div className="chat-layout"><section className="panel chat-panel"><div className="chat-intro"><div className="chat-symbol"><Bot size={30}/></div><div><h2>Ask the knowledge base</h2><p>Answers are returned only after retrieval confidence and citation checks pass.</p></div></div>{bids.length > 0 && <label className="field"><span>Bid scope</span><select value={bidId} onChange={(event) => setBidId(event.target.value)}><option value="">All bids</option>{bids.map((bid) => <option value={bid.id} key={bid.id}>{bid.bid_key}</option>)}</select></label>}{disabled && <div className="alert"><Database size={18}/>Index at least one document before asking questions.</div>}{(answer || busy) && <article className="answer-card"><div className="answer-top"><span className="assistant-label"><Sparkles size={15}/>Grounded answer</span>{result && <StatusBadge status={result.status}/>}</div><div className="answer-text">{answer || <span className="thinking"><LoaderCircle className="spin" size={18}/>Retrieving and validating evidence…</span>}</div>{result && <div className="answer-meta"><span><CircleGauge size={15}/>{Math.round(result.confidence * 100)}% confidence</span><span><Activity size={15}/>{Math.round(result.latency_ms.total_ms || 0)} ms</span><span><Bot size={15}/>{result.model_used || 'No model call'}</span><span><Database size={15}/>{result.route}</span></div>}</article>}{error && <div className="alert error"><XCircle size={18}/>{error}</div>}<div className="composer"><textarea value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void ask() } }} placeholder="Ask a question grounded in your documents…" disabled={disabled}/><button className="send" disabled={disabled || busy || !query.trim()} onClick={() => void ask()} aria-label="Send"><Send size={19}/></button><small>Enter to send · Shift + Enter for a new line</small></div></section><aside className="panel sources"><div className="panel-heading"><div><p className="eyebrow">Evidence</p><h3>Sources</h3></div><span className="count">{citations.length}</span></div>{citations.length === 0 ? <Empty icon={BookOpenText} title="No citations yet" text="Retrieved evidence will appear here with exact source locations."/> : citations.map((citation, index) => <article className="citation" key={citation.chunk_id}><span>{index + 1}</span><div><strong>{citation.document}</strong><small>{[citation.page && `Page ${citation.page}`, citation.section].filter(Boolean).join(' · ') || 'Document excerpt'}</small><p>{citation.quote}</p><code>{citation.chunk_id}</code></div></article>)}</aside></div>
}

function Evaluation() {
  const [run, setRun] = useState<{ id: string; status: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const start = async () => { setBusy(true); try { setRun(await api.runEvaluation()) } finally { setBusy(false) } }
  const metrics = ['Context precision', 'Context recall', 'Faithfulness', 'Answer relevancy', 'MRR / NDCG', 'P50 / P95 latency']
  return <div className="stack"><section className="hero compact"><div><span className="kicker"><BarChart3 size={15}/>Regression quality gate</span><h2>Measure retrieval and generation.</h2><p>Run native retrieval metrics alongside RAGAS and DeepEval using the versioned evaluation dataset.</p><button className="primary" disabled={busy} onClick={() => void start()}>{busy ? <LoaderCircle className="spin" size={17}/> : <BarChart3 size={17}/>}Run evaluation</button></div><CircleGauge size={74} className="hero-watermark"/></section>{run && <div className="alert"><CheckCircle2 size={18}/>Evaluation {run.id.slice(0, 8)} queued. Status: {run.status}</div>}<section className="metric-grid">{metrics.map((metric) => <article className="panel metric" key={metric}><div className="metric-icon"><Activity size={19}/></div><strong>{metric}</strong><span>Included in JSON and HTML reports</span></article>)}</section><section className="panel"><div className="panel-heading"><div><p className="eyebrow">Dataset contract</p><h3>Expected fields</h3></div></div><pre className="code-block">{`{\n  "question": "...",\n  "expected_answer": "...",\n  "expected_sources": ["document.pdf"]\n}`}</pre></section></div>
}

function SystemHealth({ health }: { health: Health | null }) {
  const dependencies = health?.dependencies || { postgres: 'unknown', redis: 'unknown', pinecone: 'unknown', openai: 'unknown' }
  return <div className="stack"><section className="hero compact"><div><span className="kicker"><ServerCog size={15}/>Local runtime</span><h2>{health?.status === 'ready' ? 'Every dependency is ready.' : 'Complete the local setup.'}</h2><p>Readiness verifies credentials and live connectivity without exposing secrets.</p></div><div className={`health-ring ${health?.status === 'ready' ? 'ready' : ''}`}><Activity size={32}/></div></section><section className="dependency-grid">{Object.entries(dependencies).map(([name, state]) => <article className="panel dependency" key={name}><div><Database size={20}/><strong>{name}</strong></div><StatusBadge status={state}/></article>)}</section><section className="panel"><div className="panel-heading"><div><p className="eyebrow">Runtime details</p><h3>Application</h3></div></div><dl className="detail-list"><div><dt>Version</dt><dd>{health?.version || '—'}</dd></div><div><dt>Environment</dt><dd>Local Docker Compose</dd></div><div><dt>API documentation</dt><dd><a href="/docs">/docs</a></dd></div><div><dt>Metrics</dt><dd><a href="/metrics">/metrics</a></dd></div></dl></section></div>
}

function Empty({ icon: Icon, title, text }: { icon: typeof Database; title: string; text: string }) {
  return <div className="empty"><Icon size={28}/><strong>{title}</strong><span>{text}</span></div>
}

export default App
