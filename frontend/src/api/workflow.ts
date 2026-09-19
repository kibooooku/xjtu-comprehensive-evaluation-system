import { authorization, type Credentials } from './declarations'

export interface WorkflowState {
  id: number
  status: 'DRAFT' | 'PENDING' | 'APPROVED' | 'REJECTED'
  submissionVersion: number
  documentVersion: number
}

export interface QueueItem {
  id: number
  title: string
  submissionVersion: number
  ownerId: number
  ownerUsername: string
  ownerDisplayName: string
  classId: number
  className: string
  updatedAt: string
}

export type ReasonCode =
  | 'ACTIVITY_INVALID'
  | 'EVIDENCE_INVALID'
  | 'IDENTITY_NOT_FOUND'
  | 'OTHER'

export interface ReviewEntry {
  id: number
  declarationId: number
  reviewerUserId: number
  submissionVersion: number
  result: 'APPROVED' | 'REJECTED'
  reasonCode: ReasonCode | null
  customReason: string | null
  reviewerName: string
  reviewedAt: string
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
  return response.json() as Promise<T>
}

export const workflowApi = {
  submit: (credentials: Credentials, id: number, documentVersion: number) =>
    request<WorkflowState>(`/api/declarations/${id}/submit`, credentials, {
      method: 'POST', headers: { 'If-Match': String(documentVersion) },
    }),
  revise: (credentials: Credentials, id: number, submissionVersion: number) =>
    request<WorkflowState>(`/api/declarations/${id}/revise`, credentials, {
      method: 'POST', headers: { 'If-Match': String(submissionVersion) },
    }),
  history: (credentials: Credentials, id: number) =>
    request<ReviewEntry[]>(`/api/declarations/${id}/reviews`, credentials),
  queue: (credentials: Credentials, classId: number) =>
    request<QueueItem[]>(`/api/review/classes/${classId}/pending`, credentials),
  decide: (credentials: Credentials, id: number, input: {
    submissionVersion: number
    result: 'APPROVED' | 'REJECTED'
    reasonCode?: ReasonCode
    customReason?: string
  }) => request<WorkflowState>(`/api/review/declarations/${id}/decision`, credentials, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  }),
}
