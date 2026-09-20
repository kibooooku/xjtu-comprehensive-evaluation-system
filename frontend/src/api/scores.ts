import { authorization, type Credentials } from './declarations'

export type Category = 'ABILITY_EXPANSION'
export type Subcategory = 'ACADEMIC_RESEARCH_INNOVATION'
export type ItemType = 'DISCIPLINE_COMPETITION'
export type Level = 'HIGH_LEVEL_INTERNATIONAL' | 'NATIONAL' | 'PROVINCIAL' | 'SCHOOL' | 'LOCAL_AUTHORITY' | 'INDUSTRY_ENTERPRISE' | 'SOCIETY_ASSOCIATION'
export type Award = 'SPECIAL' | 'FIRST' | 'SECOND' | 'THIRD' | 'EXCELLENCE'
export interface ScoreInput {
  activityName: string
  category: Category
  subcategory: Subcategory
  itemType: ItemType
  level: Level
  award: Award
}
export interface ScoreRule extends Omit<ScoreInput, 'activityName'> {
  id: number
  ruleSetVersion: string
  score: number
  sourceDocument: string
  sourceLocator: string
}
export interface ScoreItem extends ScoreInput {
  id: number
  declarationId: number
  calculatedScore: number
  ruleId: number
  projectKey: string | null
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
const base = (id: number) => `/api/declarations/${id}/score-items`
export const scoreApi = {
  rules: (credentials: Credentials, classId: number) => request<ScoreRule[]>('/api/score-rules?classId=' + classId, credentials),
  list: (credentials: Credentials, id: number) => request<ScoreItem[]>(base(id), credentials),
  create: (credentials: Credentials, id: number, version: number, input: ScoreInput) =>
    request<ScoreItem>(base(id), credentials, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'If-Match': String(version) },
      body: JSON.stringify(input),
    }),
  update: (credentials: Credentials, id: number, itemId: number, version: number, input: ScoreInput) =>
    request<ScoreItem>(`${base(id)}/${itemId}`, credentials, {
      method: 'PUT', headers: { 'Content-Type': 'application/json', 'If-Match': String(version) },
      body: JSON.stringify(input),
    }),
  delete: (credentials: Credentials, id: number, itemId: number, version: number) =>
    request<void>(`${base(id)}/${itemId}`, credentials, {
      method: 'DELETE', headers: { 'If-Match': String(version) },
    }),
}
