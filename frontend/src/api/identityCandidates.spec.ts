import { afterEach, describe, expect, it, vi } from 'vitest'
import { identityCandidateApi } from './identityCandidates'

const credentials = { username: 'fictional-owner', password: 'test-password' }

describe('identity candidate API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uses private authenticated GET and current document version on confirmation', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ candidates: [], documentVersion: 3 }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await identityCandidateApi.list(credentials, 42)
    await identityCandidateApi.confirm(credentials, 42, 'candidate/fictional', 3)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [listPath, listInit] = fetchMock.mock.calls[0]!
    const [confirmPath, confirmInit] = fetchMock.mock.calls[1]!
    expect(listPath).toBe('/api/declarations/42/identity-candidates')
    expect(confirmPath).toBe('/api/declarations/42/identity-candidates/candidate%2Ffictional/confirm')
    expect(listInit.headers.Authorization).toBe('Basic ' + btoa('fictional-owner:test-password'))
    expect(confirmInit.headers.Authorization).toBe(listInit.headers.Authorization)
    expect(confirmInit.method).toBe('POST')
    expect(confirmInit.headers['If-Match']).toBe('3')
  })
})
