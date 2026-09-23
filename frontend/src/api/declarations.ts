export interface Membership {
  classId: number
  classCode: string
  className: string
  role: 'STUDENT' | 'CLASS_COMMITTEE'
}

export interface Me {
  id: number
  username: string
  displayName: string
  studentNumber: string | null
  studentName: string | null
  memberships: Membership[]
}

export interface Declaration {
  id: number
  classId: number
  className: string
  title: string
  status: 'DRAFT' | 'PENDING' | 'APPROVED' | 'REJECTED'
  hasPdf: boolean
  pdf: null | { originalFilename: string; sizeBytes: number; sha256: string; documentVersion: number; pageCount: number | null; analysisStatus: string }
  createdAt: string
  updatedAt: string
  submissionVersion: number
  latestReasonCode: string | null
  latestCustomReason: string | null
}

export interface Credentials {
  username: string
  password: string
}

export function authorization(credentials: Credentials): string {
  const bytes = new TextEncoder().encode(`${credentials.username}:${credentials.password}`)
  const binary = Array.from(bytes, (byte) => String.fromCharCode(byte)).join('')
  return `Basic ${btoa(binary)}`
}

async function request<T>(
  path: string,
  credentials: Credentials,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Authorization: authorization(credentials),
      ...init.headers,
    },
  })
  if (!response.ok) {
    const problem = await response.json().catch(() => null)
    throw new Error(problem?.detail ?? problem?.title ?? `请求失败（${response.status}）`)
  }
  return response.json() as Promise<T>
}

export const declarationApi = {
  me: (credentials: Credentials) => request<Me>('/api/me', credentials),
  updateIdentity: (credentials: Credentials, studentNumber: string, studentName: string) =>
    request<Me>('/api/me/identity', credentials, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ studentNumber, studentName }),
    }),
  mine: (credentials: Credentials) =>
    request<Declaration[]>('/api/declarations/mine', credentials),
  get: (credentials: Credentials, id: number) =>
    request<Declaration>('/api/declarations/' + id, credentials),
  create: (credentials: Credentials, classId: number, title: string) =>
    request<Declaration>('/api/declarations', credentials, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ classId, title }),
    }),
  updateTitle: (credentials: Credentials, id: number, title: string) =>
    request<Declaration>('/api/declarations/' + id, credentials, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }),
  uploadPdf: (credentials: Credentials, id: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Declaration>(`/api/declarations/${id}/pdf`, credentials, {
      method: 'POST',
      body: form,
    })
  },
  replacePdf: (credentials: Credentials, id: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Declaration>('/api/declarations/' + id + '/pdf', credentials, {
      method: 'PUT',
      body: form,
    })
  },
  async pdf(credentials: Credentials, id: number): Promise<Blob> {
    const response = await fetch(`/api/declarations/${id}/pdf`, {
      headers: { Authorization: authorization(credentials) },
    })
    if (!response.ok) throw new Error(`PDF 获取失败（${response.status}）`)
    return response.blob()
  },
}
