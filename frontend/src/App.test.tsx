import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

describe('Advance RAG app', () => {
  afterEach(() => {
    window.sessionStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders the overview and navigation', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/ready')) {
        return Promise.resolve(new Response(JSON.stringify({ status: 'ready', version: '1.0.0', dependencies: {} }), { status: 200 }))
      }
      return Promise.resolve(new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }))
    }))
    render(<App />)
    expect(screen.getByText('Your documents, answered with proof.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /documents/i })).toBeInTheDocument()
    expect(await screen.findByText('Ready')).toBeInTheDocument()
  })

  it('sends the session API key to protected endpoints', async () => {
    window.sessionStorage.setItem('advance-rag-api-key', 'test-app-key')
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      void init
      const url = String(input)
      if (url.endsWith('/ready')) {
        return Promise.resolve(new Response(JSON.stringify({ status: 'ready', version: '1.0.0', dependencies: {} }), { status: 200 }))
      }
      return Promise.resolve(new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/documents?knowledge_base_id=default', expect.objectContaining({ headers: expect.any(Headers) })))
    const documentCall = fetchMock.mock.calls.find(([input]) => String(input).includes('knowledge_base_id=default'))
    const headers = documentCall?.[1]?.headers as Headers
    expect(headers.get('X-API-Key')).toBe('test-app-key')
  })

  it('enables bid chat when only the RFP knowledge base has active documents', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/ready')) return Promise.resolve(new Response(JSON.stringify({ status: 'ready', version: '1.0.0', dependencies: {} }), { status: 200 }))
      if (url.includes('knowledge_base_id=rfp-intelligence')) return Promise.resolve(new Response(JSON.stringify({ items: [{ id: 'doc-1', title: 'Bid document', source_name: 'bid.pdf', source_type: 'pdf', source_url: null, mime_type: 'application/pdf', status: 'active', current_version: 1, content_hash: 'hash', size_bytes: 100, created_at: '2026-01-01', updated_at: '2026-01-01' }], total: 1 }), { status: 200 }))
      if (url.includes('/api/v1/bids')) return Promise.resolve(new Response(JSON.stringify({ items: [{ id: 'bid-id', bid_key: 'PackageA', title: 'Package A', knowledge_base_id: 'rfp-intelligence', source_folder: null, status: 'active', extra_metadata: {}, created_at: '2026-01-01', updated_at: '2026-01-01' }], total: 1 }), { status: 200 }))
      return Promise.resolve(new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }))
    }))
    render(<App />)
    await screen.findAllByText('Ready')
    screen.getAllByRole('button', { name: /grounded chat/i }).at(-1)?.click()
    const composers = await screen.findAllByPlaceholderText('Ask a question grounded in your documents…')
    expect(composers.at(-1)).not.toBeDisabled()
    expect(screen.queryAllByText('Index at least one document before asking questions.')).toHaveLength(0)
  })
})
