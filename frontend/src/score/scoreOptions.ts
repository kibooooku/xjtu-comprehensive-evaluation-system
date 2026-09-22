import type { Award, Category, ItemType, Level, ScoreRule, Subcategory } from '@/api/scores'

export type Choice = { category?: Category; subcategory?: Subcategory; itemType?: ItemType; level?: Level; award?: Award }
export type ChoiceField = 'category' | 'subcategory' | 'itemType' | 'level' | 'award'
const order: ChoiceField[] = ['category', 'subcategory', 'itemType', 'level', 'award']
export function optionsFor(rules: ScoreRule[], field: ChoiceField, choice: Choice): string[] {
  const predecessors = order.slice(0, order.indexOf(field))
  return [...new Set(rules.filter((rule) => predecessors.every((key) => !choice[key] || rule[key] === choice[key]))
    .map((rule) => rule[field]))]
}
export function previewScore(rules: ScoreRule[], choice: Choice): number | null {
  const match = rules.find((rule) => order.every((key) => choice[key] && rule[key] === choice[key]))
  return match?.score ?? null
}
export const labels: Record<string, string> = {
  ABILITY_EXPANSION: '能力拓展', ACADEMIC_RESEARCH_INNOVATION: '学术科研及创新创业',
  DISCIPLINE_COMPETITION: '学科竞赛', HIGH_LEVEL_INTERNATIONAL: '高水平国际',
  NATIONAL: '国家级', PROVINCIAL: '省级', SCHOOL: '校级', LOCAL_AUTHORITY: '地方行政部门',
  INDUSTRY_ENTERPRISE: '行业/企业', SOCIETY_ASSOCIATION: '学会/协会',
  SPECIAL: '特等奖', FIRST: '一等奖', SECOND: '二等奖', THIRD: '三等奖', EXCELLENCE: '优秀奖',
}
