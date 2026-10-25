export type View = 'overview' | 'bids' | 'documents' | 'chat' | 'evaluation' | 'system'

export interface Citation {
  document_id: string
  document: string
  chunk_id: string
  page?: number
  section?: string
  source_url?: string
  quote?: string
}

export interface QueryResponse {
  answer: string
  status: string
  confidence: number
  citations: Citation[]
  route: string
  model_used?: string
  cache_hit: boolean
  retrieval_metadata: {
    dense_results: number
    bm25_results: number
    fused_results: number
    reranked_results: number
    retrieval_attempts: number
    top_hits?: RetrievalDiagnostic[]
  }
  latency_ms: Record<string, number>
  token_usage: Record<string, number>
  request_id: string
}

export interface RetrievalDiagnostic {
  chunk_id: string
  page?: number
  source: string
  fusion_score: number
  reranker_score: number
  query_variant: string
}

export interface DocumentItem {
  id: string
  title: string
  source_name: string
  source_type: string
  source_url?: string
  mime_type: string
  status: string
  current_version: number
  content_hash: string
  size_bytes: number
  bid_id?: string
  doc_type?: string
  addendum_number?: number
  document_date?: string
  relative_path?: string
  created_at: string
  updated_at: string
}

export interface DocumentList {
  items: DocumentItem[]
  total: number
}

export interface Health {
  status: string
  version: string
  dependencies?: Record<string, string>
}

export interface BidItem {
  id: string
  bid_key: string
  title: string
  knowledge_base_id: string
  source_folder?: string
  status: string
  extra_metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface BidDetail extends BidItem {
  documents: DocumentItem[]
}

export interface SourceRef {
  document_id: string
  file: string
  page?: number
  chunk_id: string
  quote: string
  doc_type?: string
  addendum_number?: number
}

export interface BidFieldResult {
  value: unknown
  sources: SourceRef[]
  confidence: number
  notes: string
  status: string
}

export interface BidRecord {
  bid_id: string
  fields: Record<string, BidFieldResult>
  addendum_changes: Array<{
    field: string
    previous_value: unknown
    revised_value: unknown
    source: SourceRef
    rationale: string
  }>
  validation: {
    passed: number
    failed: number
    not_found: number
    issues: Array<{ field: string; code: string; message: string }>
  }
  run_metadata: Record<string, unknown>
}

export interface ExtractionRun {
  id: string
  bid_id: string
  status: string
  output_json: BidRecord | Record<string, never>
  validation_json: Record<string, unknown>
  trace_json: Array<Record<string, unknown>>
  token_usage: Record<string, number>
  latency_ms: Record<string, number>
  artifact_path?: string
  error?: string
  created_at: string
  updated_at: string
}
