import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElSelect } from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CommitteeReviewPanel from './CommitteeReviewPanel.vue'

const mocks = vi.hoisted(() => ({
  queue: vi.fn(),
  decide: vi.fn(),
  get: vi.fn(),
}))
vi.mock('@/api/workflow', () => ({ workflowApi: { queue: mocks.queue, decide: mocks.decide } }))
vi.mock('@/api/declarations', () => ({ declarationApi: { get: mocks.get } }))
vi.mock('./EvidenceEditor.vue', () => ({
  default: { props: { readOnly: Boolean }, template: '<div data-test="review-evidence">{{ readOnly ? "只读" : "可写" }}</div>' },
}))

vi.mock('./ScoreItemsPanel.vue', () => ({
  default: { props: { readOnly: Boolean }, template: '<div data-test="review-score">{{ readOnly ? "只读评分摘要" : "可编辑评分" }}</div>' },
}))

const credentials = { username: 'committee', password: 'test-password' }
const memberships = [{ classId: 10, classCode: 'DEMO-01', className: '虚构一班', role: 'CLASS_COMMITTEE' as const }]
const pending = {
  id: 42, title: '虚构证明', submissionVersion: 2, ownerId: 1,
  ownerUsername: 'owner', ownerDisplayName: '虚构学生甲',
  classId: 10, className: '虚构一班', updatedAt: '2026-01-01T00:00:00Z',
}
const declaration = {
  ...pending, status: 'PENDING' as const, hasPdf: true,
  pdf: { originalFilename: 'evidence.pdf', sizeBytes: 20, sha256: 'fictional', documentVersion: 1 },
  latestReasonCode: null, latestCustomReason: null,
  createdAt: '2026-01-01T00:00:00Z',
}

function action(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text().includes(text))
  expect(button).toBeDefined()
  return button!
}

async function openPanel() {
  const wrapper = mount(CommitteeReviewPanel, {
    props: { credentials, memberships },
    global: { plugins: [ElementPlus] },
  })
  await flushPromises()
  await action(wrapper, '查看材料并审核').trigger('click')
  await flushPromises()
  return wrapper
}

describe('CommitteeReviewPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.queue.mockResolvedValue([pending])
    mocks.get.mockResolvedValue(declaration)
    mocks.decide.mockResolvedValue({ id: 42, status: 'APPROVED', submissionVersion: 2, documentVersion: 1 })
  })

  it('loads only its committee queue and approves the selected submission version', async () => {
    const wrapper = await openPanel()
    expect(mocks.queue).toHaveBeenCalledWith(credentials, 10)
    expect(mocks.get).toHaveBeenCalledWith(credentials, 42)
    expect(wrapper.text()).toContain('虚构学生甲')
    expect(wrapper.get('[data-test="review-evidence"]').text()).toBe('只读')
    await action(wrapper, '审核通过').trigger('click')
    await flushPromises()
    expect(mocks.decide).toHaveBeenCalledWith(credentials, 42, {
      submissionVersion: 2, result: 'APPROVED',
    })
    expect(mocks.queue).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('refreshes a stale queue item instead of showing review controls', async () => {
    mocks.get.mockResolvedValueOnce({ ...declaration, status: 'APPROVED' })
    const wrapper = mount(CommitteeReviewPanel, {
      props: { credentials, memberships },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()
    await action(wrapper, '查看材料并审核').trigger('click')
    await flushPromises()
    expect(mocks.queue).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('待审核列表已变化')
    expect(wrapper.text()).not.toContain('审核通过')
    expect(mocks.decide).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('refreshes the queue when a reviewed declaration is no longer readable', async () => {
    mocks.get.mockRejectedValueOnce(new Error('申报不存在'))
    const wrapper = mount(CommitteeReviewPanel, {
      props: { credentials, memberships },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()
    await action(wrapper, '查看材料并审核').trigger('click')
    await flushPromises()
    expect(mocks.queue).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('待审核列表已变化')
    expect(wrapper.text()).not.toContain('审核通过')
    wrapper.unmount()
  })
  it('requires a custom reason for OTHER and sends it with rejection', async () => {
    const wrapper = await openPanel()
    const selects = wrapper.findAllComponents(ElSelect)
    expect(selects).toHaveLength(2)
    selects[1]!.vm.$emit('update:modelValue', 'OTHER')
    await flushPromises()
    await action(wrapper, '驳回申报').trigger('click')
    await flushPromises()
    expect(mocks.decide).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('需要填写具体原因')

    await wrapper.get('textarea').setValue('  虚构材料需补充签章  ')
    await action(wrapper, '驳回申报').trigger('click')
    await flushPromises()
    expect(mocks.decide).toHaveBeenCalledWith(credentials, 42, {
      submissionVersion: 2, result: 'REJECTED',
      reasonCode: 'OTHER', customReason: '虚构材料需补充签章',
    })
    wrapper.unmount()
  })
})

