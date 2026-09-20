import { describe, expect, it } from 'vitest'
import { optionsFor, previewScore } from './scoreOptions'
import type { ScoreRule } from '@/api/scores'

const base = { ruleSetVersion: 'XJTU_SCHOOL_2018_V1', category: 'ABILITY_EXPANSION',
  subcategory: 'ACADEMIC_RESEARCH_INNOVATION', itemType: 'DISCIPLINE_COMPETITION',
  sourceDocument: '虚构测试规则', sourceLocator: '第6页' } as const
const rules: ScoreRule[] = [
  { ...base, id: 1, level: 'NATIONAL', award: 'FIRST', score: 10 },
  { ...base, id: 2, level: 'NATIONAL', award: 'SECOND', score: 9 },
  { ...base, id: 3, level: 'PROVINCIAL', award: 'FIRST', score: 8 },
  { ...base, id: 4, level: 'SCHOOL', award: 'EXCELLENCE', score: 1 },
]

describe('score rule choices', () => {
  it('cascades from category to the awards supported at the selected level', () => {
    expect(optionsFor(rules, 'category', {})).toEqual(['ABILITY_EXPANSION'])
    expect(optionsFor(rules, 'subcategory', { category: 'ABILITY_EXPANSION' }))
      .toEqual(['ACADEMIC_RESEARCH_INNOVATION'])
    expect(optionsFor(rules, 'level', { category: 'ABILITY_EXPANSION', subcategory: 'ACADEMIC_RESEARCH_INNOVATION', itemType: 'DISCIPLINE_COMPETITION' }))
      .toEqual(['NATIONAL', 'PROVINCIAL', 'SCHOOL'])
    expect(optionsFor(rules, 'award', { level: 'SCHOOL' })).toEqual(['EXCELLENCE'])
    expect(optionsFor(rules, 'award', { level: 'NATIONAL' })).toEqual(['FIRST', 'SECOND'])
  })
  it('only previews a complete exact rule match', () => {
    expect(previewScore(rules, { category: 'ABILITY_EXPANSION', subcategory: 'ACADEMIC_RESEARCH_INNOVATION', itemType: 'DISCIPLINE_COMPETITION', level: 'NATIONAL', award: 'FIRST' })).toBe(10)
    expect(previewScore(rules, { category: 'ABILITY_EXPANSION', subcategory: 'ACADEMIC_RESEARCH_INNOVATION', itemType: 'DISCIPLINE_COMPETITION', level: 'SCHOOL', award: 'FIRST' })).toBeNull()
    expect(previewScore(rules, { level: 'NATIONAL', award: 'FIRST' })).toBeNull()
  })
})
