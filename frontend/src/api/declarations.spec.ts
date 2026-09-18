import { describe, expect, it, vi } from 'vitest'

import { declarationApi } from './declarations'

describe('declarationApi.pdf', () => {
  it('downloads the private PDF as a Blob with Basic authentication', async () => {
    const blob = new Blob(['%PDF-1.4'], { type: 'application/pdf' })
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: vi.fn().mockResolvedValue(blob),
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      declarationApi.pdf({ username: 'student-a', password: 'test-password' }, 42),
    ).resolves.toBe(blob)
    expect(fetchMock).toHaveBeenCalledWith('/api/declarations/42/pdf', {
      headers: { Authorization: `Basic ${btoa('student-a:test-password')}` },
    })
  })
})
