import { authorization, type Credentials } from './declarations'
import type { EvidenceRegion } from './evidence'

export type AnalysisStatus = 'TEXT_AVAILABLE' | 'NO_TEXT' | 'FAILED'
export interface IdentityCandidate {
  id: string
  pageNumber: number
  x: number
  y: number
  width: number
  height: number
  matchedText: string
  matchedBy: 'STUDENT_NUMBER' | 'NAME'
  source: 'PDF_TEXT'
  certainty: 'HIGH' | 'MULTIPLE'
}
export interface CandidateResult {
  documentVersion: number
  pageCount: number | null
  analysisStatus: AnalysisStatus
  candidates: IdentityCandidate[]
}
async function request<T>(path: string, credentials: Credentials, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init, headers: { Authorization: authorization(credentials), ...init.headers },
  })
  if (!response.ok) {
    const problem = await response.json().catch(() => null)
    throw new Error(problem?.detail ?? `请求失败（${response.status}）`)
  }
  return response.json() as Promise<T>
}
const path = (id: number) => `/api/declarations/${id}/identity-candidates`
export const identityCandidateApi = {
  list: (credentials: Credentials, id: number) => request<CandidateResult>(path(id), credentials),
  confirm: (credentials: Credentials, id: number, candidateId: string, documentVersion: number) =>
    request<EvidenceRegion>(`${path(id)}/${encodeURIComponent(candidateId)}/confirm`, credentials, {
      method: 'POST', headers: { 'If-Match': String(documentVersion) },
    }),
}
