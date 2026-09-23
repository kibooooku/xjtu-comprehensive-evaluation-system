import { authorization, type Credentials } from './declarations'
import type { NormalizedRect } from '@/evidence/coordinates'

export type EvidenceType = 'IDENTITY' | 'VALIDITY'
export type EvidenceSource = 'MANUAL' | 'OCR' | 'PDF_TEXT_AUTO'

export interface EvidenceInput extends NormalizedRect {
  type: EvidenceType
  pageNumber: number
}

export interface EvidenceRegion extends EvidenceInput {
  id: number
  documentId: number
  source: EvidenceSource
  createdAt: string
  updatedAt: string
}

async function request<T>(path: string, credentials: Credentials, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Authorization: authorization(credentials), ...init.headers },
  })
  if (!response.ok) {
    const problem = await response.json().catch(() => null)
    throw new Error(problem?.detail ?? problem?.title ?? `请求失败（${response.status}）`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

function base(id: number): string {
  return `/api/declarations/${id}/evidence-regions`
}

export const evidenceApi = {
  list: (credentials: Credentials, declarationId: number) =>
    request<EvidenceRegion[]>(base(declarationId), credentials),
  create: (credentials: Credentials, declarationId: number, documentVersion: number, input: EvidenceInput) =>
    request<EvidenceRegion>(base(declarationId), credentials, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'If-Match': String(documentVersion) },
      body: JSON.stringify(input),
    }),
  update: (credentials: Credentials, declarationId: number, regionId: number, documentVersion: number, input: EvidenceInput) =>
    request<EvidenceRegion>(`${base(declarationId)}/${regionId}`, credentials, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'If-Match': String(documentVersion) },
      body: JSON.stringify(input),
    }),
  delete: (credentials: Credentials, declarationId: number, regionId: number, documentVersion: number) =>
    request<void>(`${base(declarationId)}/${regionId}`, credentials, { method: 'DELETE', headers: { 'If-Match': String(documentVersion) } }),
}
