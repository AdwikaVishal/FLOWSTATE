import { describe, expect, it, vi } from 'vitest'
import { ApiError, request } from './client'

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

describe('API client', () => {
  it('requests versioned API paths with actor and accept headers', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: 'ok' }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await request<{ status: string }>('/health')

    expect(result).toEqual({ status: 'ok' })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/health')
    const headers = init.headers as Headers
    expect(headers.get('Accept')).toBe('application/json')
    expect(headers.get('X-Flowstate-Actor')).toBe('console-operator')
  })

  it('maps problem+json failures into a typed ApiError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            type: 'https://flowstate.dev/problems/version_not_published',
            title: 'Version Not Published',
            status: 409,
            code: 'VERSION_NOT_PUBLISHED',
            detail: 'Only a PUBLISHED version can execute.',
            instance: 'urn:request:req-123',
            errors: { version: 'draft' },
          },
          409,
          { 'X-Request-ID': 'header-id' },
        ),
      ),
    )

    try {
      await request('/runs')
      throw new Error('request should have failed')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      const apiError = error as ApiError
      expect(apiError.status).toBe(409)
      expect(apiError.code).toBe('VERSION_NOT_PUBLISHED')
      expect(apiError.message).toBe('Only a PUBLISHED version can execute.')
      expect(apiError.requestId).toBe('req-123')
      expect(apiError.details).toEqual({ version: 'draft' })
    }
  })

  it('falls back to the request id header when the body has no instance', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({ code: 'HTTP_404', detail: 'Missing' }, 404, { 'X-Request-ID': 'from-header' }),
      ),
    )

    await expect(request('/missing')).rejects.toMatchObject({
      requestId: 'from-header',
      status: 404,
    })
  })

  it('reports an honest network error when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(request('/health')).rejects.toMatchObject({
      code: 'NETWORK_ERROR',
      status: 0,
    })
  })
})