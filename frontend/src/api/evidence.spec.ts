import { afterEach, describe, expect, it, vi } from 'vitest'
import { evidenceApi } from './evidence'

const credentials = { username: 'owner', password: 'test-password' }
const input = { type: 'IDENTITY' as const, pageNumber: 1, x: 0.1, y: 0.2, width: 0.3, height: 0.4 }

describe('evidence API version contract', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends If-Match for create, update, and delete', async () => {
    const fetchMock = vi.fn().mockImplementation(async (_path, init: RequestInit) => ({
      ok: true,
      status: init.method === 'DELETE' ? 204 : 200,
      json: async () => ({ id: 7 }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await evidenceApi.create(credentials, 42, 3, input)
    await evidenceApi.update(credentials, 42, 7, 3, input)
    await evidenceApi.delete(credentials, 42, 7, 3)

    expect(fetchMock).toHaveBeenCalledTimes(3)
    const calls = fetchMock.mock.calls
    expect(calls.map(([path, init]) => [path, init.method, init.headers['If-Match']])).toEqual([
      ['/api/declarations/42/evidence-regions', 'POST', '3'],
      ['/api/declarations/42/evidence-regions/7', 'PUT', '3'],
      ['/api/declarations/42/evidence-regions/7', 'DELETE', '3'],
    ])
    for (const [, init] of calls) {
      expect(init.headers.Authorization).toBe('Basic ' + btoa('owner:test-password'))
    }
  })
})
