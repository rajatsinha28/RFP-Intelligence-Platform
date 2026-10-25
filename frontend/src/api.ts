import type { BidDetail, BidItem, BidRecord, DocumentList, ExtractionRun, Health, QueryResponse } from './types'

const API_KEY_STORAGE_KEY = 'advance-rag-api-key'

export function getStoredApiKey(): string {
  try {
    return window.sessionStorage.getItem(API_KEY_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

export function setStoredApiKey(value: string): void {
  try {
    if (value) window.sessionStorage.setItem(API_KEY_STORAGE_KEY, value)
    else window.sessionStorage.removeItem(API_KEY_STORAGE_KEY)
  } catch {
    // Requests still work when authentication is disabled and storage is unavailable.
  }
}

function requestHeaders(json = false): Headers {
  const headers = new Headers()
  if (json) headers.set('Content-Type', 'application/json')
  const apiKey = getStoredApiKey()
  if (apiKey) headers.set('X-API-Key', apiKey)
  return headers
}

async function checked<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(data.detail || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => fetch('/ready').then((response) => checked<Health>(response)),
  documents: (knowledgeBaseId = 'default') => fetch(`/api/v1/documents?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}`, { headers: requestHeaders() }).then((response) => checked<DocumentList>(response)),
  bids: () => fetch('/api/v1/bids?knowledge_base_id=rfp-intelligence', { headers: requestHeaders() }).then((response) => checked<{ items: BidItem[]; total: number }>(response)),
  bid: (bidKey: string) => fetch(`/api/v1/bids/${encodeURIComponent(bidKey)}`, { headers: requestHeaders() }).then((response) => checked<BidDetail>(response)),
  importBidFolder: async (path: string) => checked<{ bids: BidItem[]; document_jobs: unknown[]; ignored_files: string[] }>(await fetch('/api/v1/bids/import-folder', { method: 'POST', headers: requestHeaders(true), body: JSON.stringify({ path, knowledge_base_id: 'rfp-intelligence' }) })),
  uploadBidDocuments: async (bidKey: string, files: File[]) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    return checked<Array<{ document_id: string; job_id: string; status: string }>>(await fetch(`/api/v1/bids/${encodeURIComponent(bidKey)}/documents`, { method: 'POST', headers: requestHeaders(), body: form }))
  },
  startExtraction: async (bidKey: string) => checked<{ run_id: string; bid_key: string; status: string }>(await fetch(`/api/v1/bids/${encodeURIComponent(bidKey)}/extractions`, { method: 'POST', headers: requestHeaders() })),
  extraction: (runId: string) => fetch(`/api/v1/extractions/${runId}`, { headers: requestHeaders() }).then((response) => checked<ExtractionRun>(response)),
  bidRecord: (bidKey: string) => fetch(`/api/v1/bids/${encodeURIComponent(bidKey)}/record`, { headers: requestHeaders() }).then((response) => checked<BidRecord>(response)),
  upload: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return checked<{ document_id: string; job_id: string; status: string }>(
      await fetch('/api/v1/documents/upload', { method: 'POST', headers: requestHeaders(), body: form }),
    )
  },
  ingestUrl: async (url: string) =>
    checked<{ document_id: string; job_id: string; status: string }>(
      await fetch('/api/v1/documents/ingest', {
        method: 'POST',
        headers: requestHeaders(true),
        body: JSON.stringify({ url, knowledge_base_id: 'default' }),
      }),
    ),
  deleteDocument: async (id: string) => {
    const response = await fetch(`/api/v1/documents/${id}`, { method: 'DELETE', headers: requestHeaders() })
    if (!response.ok) throw new Error('Could not delete the document')
  },
  runEvaluation: async () =>
    checked<{ id: string; status: string }>(
      await fetch('/api/v1/evaluation/run', {
        method: 'POST',
        headers: requestHeaders(true),
        body: JSON.stringify({
          dataset_path: 'evaluation/datasets/rfp_retrieval.json',
          knowledge_base_id: 'rfp-intelligence',
        }),
      }),
    ),
}

export async function streamQuery(
  query: string,
  onDelta: (text: string) => void,
  onCitation: (citation: QueryResponse['citations'][number]) => void,
  knowledgeBaseId = 'default',
  metadataFilter: Record<string, unknown> = {},
): Promise<QueryResponse> {
  const response = await fetch('/api/v1/query/stream', {
    method: 'POST',
    headers: requestHeaders(true),
    body: JSON.stringify({ query, knowledge_base_id: knowledgeBaseId, metadata_filter: metadataFilter }),
  })
  if (!response.ok || !response.body) throw new Error('Could not start the answer stream')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let completed: QueryResponse | undefined
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const events = buffer.split('\n\n')
    buffer = events.pop() || ''
    for (const raw of events) {
      const event = raw.match(/^event: (.+)$/m)?.[1]
      const dataLine = raw.match(/^data: (.+)$/m)?.[1]
      if (!event || !dataLine) continue
      const data = JSON.parse(dataLine)
      if (event === 'answer_delta') onDelta(data.text)
      if (event === 'citation') onCitation(data)
      if (event === 'done') completed = data
      if (event === 'error') throw new Error(data.message || 'Query failed')
    }
  }
  if (!completed) throw new Error('The answer stream ended unexpectedly')
  return completed
}
