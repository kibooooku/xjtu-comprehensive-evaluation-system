import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElSelect } from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ScoreItemsPanel from './ScoreItemsPanel.vue'

const api = vi.hoisted(() => ({ rules: vi.fn(), list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() }))
vi.mock('@/api/scores', () => ({ scoreApi: api }))
const credentials = { username: 'owner', password: 'test-password' }
const declaration = { id: 42, classId: 10, status: 'DRAFT' as const, submissionVersion: 0 }
const base = { ruleSetVersion: 'XJTU_SCHOOL_2018_V1', category: 'ABILITY_EXPANSION',
  subcategory: 'ACADEMIC_RESEARCH_INNOVATION', itemType: 'DISCIPLINE_COMPETITION',
  sourceDocument: '测试规则', sourceLocator: '第6页' } as const
const rules = [
  { ...base, id: 1, level: 'NATIONAL' as const, award: 'FIRST' as const, score: 10 },
  { ...base, id: 2, level: 'SCHOOL' as const, award: 'EXCELLENCE' as const, score: 1 },
]
const saved = { ...rules[0], declarationId: 42, activityName: '虚构竞赛', calculatedScore: 10,
  ruleId: 1, projectKey: null, createdAt: '2026-01-01', updatedAt: '2026-01-01' }
async function render(status: 'DRAFT' | 'PENDING' | 'APPROVED' | 'REJECTED' = 'DRAFT', readOnly = false, version = 0) {
  const wrapper = mount(ScoreItemsPanel, {
    props: { credentials, declaration: { ...declaration, status, submissionVersion: version } as never, readOnly },
    global: { plugins: [ElementPlus] },
  })
  await flushPromises()
  return wrapper
}
function button(wrapper: Awaited<ReturnType<typeof render>>, text: string) {
  const result = wrapper.findAll('button').find((item) => item.text().includes(text))
  expect(result).toBeDefined()
  return result!
}

describe('ScoreItemsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.rules.mockResolvedValue(rules)
    api.list.mockResolvedValue([])
    api.create.mockResolvedValue(saved)
    api.update.mockResolvedValue(saved)
    api.delete.mockResolvedValue(undefined)
  })
  it('shows a suggestion for the valid cascade and sends no client score', async () => {
    const wrapper = await render()
    await wrapper.get('input[aria-label="活动名称"]').setValue('虚构竞赛')
    const selects = wrapper.findAllComponents(ElSelect)
    expect(selects).toHaveLength(5)
    expect(button(wrapper, '添加加分条目').attributes('disabled')).toBeDefined()
    for (const [index, value] of ['ABILITY_EXPANSION', 'ACADEMIC_RESEARCH_INNOVATION', 'DISCIPLINE_COMPETITION', 'NATIONAL', 'FIRST'].entries()) {
      selects[index]!.vm.$emit('update:modelValue', value)
      if (index < 4) selects[index]!.vm.$emit('change', value)
      await flushPromises()
    }
    expect(wrapper.text()).toContain('建议分数：10 分')
    await button(wrapper, '添加加分条目').trigger('click')
    await flushPromises()
    expect(api.create).toHaveBeenCalledWith(credentials, 42, 0, {
      activityName: '虚构竞赛', category: 'ABILITY_EXPANSION', subcategory: 'ACADEMIC_RESEARCH_INNOVATION',
      itemType: 'DISCIPLINE_COMPETITION', level: 'NATIONAL', award: 'FIRST',
    })
    wrapper.unmount()
  })
  it.each(['PENDING', 'APPROVED', 'REJECTED'] as const)('renders %s score items without editing', async (status) => {
    api.list.mockResolvedValue([saved])
    const wrapper = await render(status)
    expect(wrapper.text()).toContain('虚构竞赛')
    expect(wrapper.text()).toContain('10 分')
    expect(wrapper.find('.score-form').exists()).toBe(false)
    expect(wrapper.findAll('button')).toHaveLength(0)
    wrapper.unmount()
  })
  it('allows a revised draft to edit and delete using its submission version', async () => {
    api.list.mockResolvedValue([saved])
    const wrapper = await render('DRAFT', false, 1)
    await button(wrapper, '编辑').trigger('click')
    await flushPromises()
    await button(wrapper, '保存修改').trigger('click')
    await flushPromises()
    expect(api.update).toHaveBeenCalledWith(credentials, 42, saved.id, 1, expect.objectContaining({ award: 'FIRST' }))
    await button(wrapper, '删除').trigger('click')
    await flushPromises()
    expect(api.delete).toHaveBeenCalledWith(credentials, 42, saved.id, 1)
    wrapper.unmount()
  })
  it('shows a read-only score summary for committee review', async () => {
    api.list.mockResolvedValue([saved])
    const wrapper = await render('PENDING', true)
    expect(wrapper.text()).toContain('系统规则建议 10 分')
    expect(wrapper.find('.score-form').exists()).toBe(false)
    wrapper.unmount()
  })
})
