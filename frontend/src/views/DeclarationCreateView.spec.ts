import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DeclarationCreateView from './DeclarationCreateView.vue'

const api = vi.hoisted(() => ({
  me: vi.fn(),
  mine: vi.fn(),
  create: vi.fn(),
  uploadPdf: vi.fn(),
  pdf: vi.fn(),
  get: vi.fn(),
  updateTitle: vi.fn(),
}))

vi.mock('@/api/declarations', () => ({ declarationApi: api }))
const workflow = vi.hoisted(() => ({ submit: vi.fn(), revise: vi.fn(), history: vi.fn() }))
vi.mock('@/api/workflow', () => ({ workflowApi: workflow }))
vi.mock('./EvidenceEditor.vue', () => ({
  default: { props: ['credentials', 'declaration', 'readOnly'], template: '<div data-test="evidence-editor">{{ declaration.title }} {{ readOnly ? "只读" : "可编辑" }}</div>' },
}))
vi.mock('./ScoreItemsPanel.vue', () => ({
  default: { props: ['credentials', 'declaration', 'readOnly'], template: '<div data-test="score-panel">{{ readOnly ? "只读评分" : "可编辑评分" }}</div>' },
}))
vi.mock('./CommitteeReviewPanel.vue', () => ({
  default: { template: '<div data-test="committee-panel">班委审核</div>' },
}))

const profile = {
  id: 1,
  username: 'student-a',
  displayName: '示例学生甲',
  memberships: [
    { classId: 10, classCode: 'DEMO-01', className: '示例一班', role: 'STUDENT' as const },
  ],
}

const draft = {
  id: 42,
  classId: 10,
  className: '示例一班',
  title: '虚构竞赛证明',
  status: 'DRAFT' as const,
  hasPdf: false,
  submissionVersion: 0,
  latestReasonCode: null,
  latestCustomReason: null,
  pdf: null,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
}

const saved = {
  ...draft,
  hasPdf: true,
  pdf: { originalFilename: 'evidence.pdf', sizeBytes: 12, sha256: 'fictional-sha', documentVersion: 1 },
}

describe('DeclarationCreateView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.me.mockResolvedValue(profile)
    api.mine.mockResolvedValue([])
    workflow.submit.mockResolvedValue({ status: 'PENDING', submissionVersion: 1, documentVersion: 1 })
    workflow.revise.mockResolvedValue({ status: 'DRAFT', submissionVersion: 1, documentVersion: 1 })
  })

  it('loads the profile and declarations using the entered credentials', async () => {
    const wrapper = mount(DeclarationCreateView, { global: { plugins: [ElementPlus] } })
    await wrapper.get('input[autocomplete="username"]').setValue(' student-a ')
    await wrapper.get('input[autocomplete="current-password"]').setValue('test-password')

    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const credentials = { username: 'student-a', password: 'test-password' }
    expect(api.me).toHaveBeenCalledWith(credentials)
    expect(api.mine).toHaveBeenCalledWith(credentials)
    expect(wrapper.text()).toContain('示例学生甲 · 新建草稿')
    expect(wrapper.text()).toContain('暂无申报')
  })

  it('creates before uploading and opens the PDF.js evidence editor', async () => {
    const callOrder: string[] = []
    api.create.mockImplementation(async () => {
      callOrder.push('create')
      return draft
    })
    api.uploadPdf.mockImplementation(async () => {
      callOrder.push('upload')
      return saved
    })

    const wrapper = mount(DeclarationCreateView, { global: { plugins: [ElementPlus] } })
    await wrapper.get('input[autocomplete="username"]').setValue('student-a')
    await wrapper.get('input[autocomplete="current-password"]').setValue('test-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    await wrapper.get('input[maxlength="200"]').setValue(' 虚构竞赛证明 ')
    const file = new File(['%PDF-1.4'], 'evidence.pdf', { type: 'application/pdf' })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [file] })
    await input.trigger('change')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const credentials = { username: 'student-a', password: 'test-password' }
    expect(callOrder).toEqual(['create', 'upload'])
    expect(api.create).toHaveBeenCalledWith(credentials, 10, '虚构竞赛证明')
    expect(api.uploadPdf).toHaveBeenCalledWith(credentials, 42, file)
    expect((input.element as HTMLInputElement).value).toBe('')
    expect(wrapper.get('[data-test="evidence-editor"]').text()).toContain('虚构竞赛证明')

    const viewButton = wrapper.findAll('button').find((button) =>
      button.text().includes('查看 PDF 与证据'),
    )
    expect(viewButton).toBeDefined()
    await viewButton!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="evidence-editor"]').text()).toContain('虚构竞赛证明')

  })

  it('keeps a created draft and the selected PDF available for retry after upload failure', async () => {
    api.create.mockResolvedValue(draft)
    api.uploadPdf
      .mockRejectedValueOnce(new Error('模拟网络中断'))
      .mockResolvedValueOnce(saved)
    api.pdf.mockResolvedValue(new Blob(['%PDF-1.4'], { type: 'application/pdf' }))

    const wrapper = mount(DeclarationCreateView, { global: { plugins: [ElementPlus] } })
    await wrapper.get('input[autocomplete="username"]').setValue('student-a')
    await wrapper.get('input[autocomplete="current-password"]').setValue('test-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    await wrapper.get('input[maxlength="200"]').setValue('虚构竞赛证明')
    const file = new File(['%PDF-1.4'], 'evidence.pdf', { type: 'application/pdf' })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [file] })
    Object.defineProperty(input.element, 'value', {
      configurable: true,
      writable: true,
      value: 'C:\\fakepath\\evidence.pdf',
    })
    await input.trigger('change')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('草稿已创建，但 PDF 上传失败')
    expect(wrapper.text()).toContain('虚构竞赛证明')
    expect(wrapper.text()).toContain('未上传 PDF')
    expect((input.element as HTMLInputElement).value).toContain('evidence.pdf')
    const retryButton = wrapper.findAll('button').find((button) =>
      button.text().includes('上传所选 PDF 到此草稿'),
    )
    expect(retryButton).toBeDefined()
    expect(retryButton!.attributes('disabled')).toBeUndefined()

    await retryButton!.trigger('click')
    await flushPromises()

    const credentials = { username: 'student-a', password: 'test-password' }
    expect(api.create).toHaveBeenCalledTimes(1)
    expect(api.uploadPdf).toHaveBeenCalledTimes(2)
    expect(api.uploadPdf).toHaveBeenLastCalledWith(credentials, 42, file)
    expect(wrapper.text()).toContain('PDF 已保存到草稿')
    expect(wrapper.text()).toContain('evidence.pdf')
    expect((input.element as HTMLInputElement).value).toBe('')
  })

  it('shows submitted material read-only and lets an owner revise a rejected submission', async () => {
    const pending = { ...saved, status: 'PENDING' as const, submissionVersion: 1 }
    const rejected = {
      ...saved, id: 43, status: 'REJECTED' as const, submissionVersion: 2,
      latestReasonCode: 'OTHER', latestCustomReason: '请补充虚构证明',
    }
    api.mine.mockResolvedValue([pending, rejected])
    api.get.mockResolvedValue({ ...rejected, status: 'DRAFT', latestReasonCode: null, latestCustomReason: null })
    const wrapper = mount(DeclarationCreateView, { global: { plugins: [ElementPlus] } })
    await wrapper.get('input[autocomplete="username"]').setValue('student-a')
    await wrapper.get('input[autocomplete="current-password"]').setValue('test-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('最近驳回原因：其他原因：请补充虚构证明')
    expect(wrapper.text()).toContain('第 2 次提交')
    const viewButtons = wrapper.findAll('button').filter((item) => item.text().includes('查看 PDF 与证据'))
    await viewButtons[0]!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="evidence-editor"]').text()).toContain('只读')
    expect(wrapper.find('.title-edit').exists()).toBe(false)
    const reviseButton = wrapper.findAll('button').find((item) => item.text().includes('修改申报'))
    expect(reviseButton).toBeDefined()
    await reviseButton!.trigger('click')
    await flushPromises()
    expect(workflow.revise).toHaveBeenCalledWith({ username: 'student-a', password: 'test-password' }, 43, 2)
    expect(wrapper.text()).toContain('申报已返回草稿')
    expect(wrapper.get('[data-test="evidence-editor"]').text()).toContain('可编辑')
  })

  it('submits a draft with the current PDF version then shows pending read-only state', async () => {
    api.mine.mockResolvedValue([saved])
    api.get.mockResolvedValue({ ...saved, status: 'PENDING', submissionVersion: 1 })
    const wrapper = mount(DeclarationCreateView, { global: { plugins: [ElementPlus] } })
    await wrapper.get('input[autocomplete="username"]').setValue('student-a')
    await wrapper.get('input[autocomplete="current-password"]').setValue('test-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const submitButton = wrapper.findAll('button').find((item) => item.text().includes('正式提交'))
    expect(submitButton).toBeDefined()
    await submitButton!.trigger('click')
    await flushPromises()
    expect(workflow.submit).toHaveBeenCalledWith({ username: 'student-a', password: 'test-password' }, 42, 1)
    expect(wrapper.text()).toContain('PENDING')
    expect(wrapper.findAll('button').some((item) => item.text().includes('正式提交'))).toBe(false)
    const viewButton = wrapper.findAll('button').find((item) => item.text().includes('查看 PDF 与证据'))
    await viewButton!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="evidence-editor"]').text()).toContain('只读')
  })

  it('shows approved submissions as read-only without draft or revision actions', async () => {
    api.mine.mockResolvedValue([{ ...saved, status: 'APPROVED', submissionVersion: 2 }])
    const wrapper = mount(DeclarationCreateView, { global: { plugins: [ElementPlus] } })
    await wrapper.get('input[autocomplete="username"]').setValue('student-a')
    await wrapper.get('input[autocomplete="current-password"]').setValue('test-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('APPROVED')
    expect(wrapper.text()).toContain('第 2 次提交')
    expect(wrapper.findAll('button').some((item) => item.text().includes('正式提交'))).toBe(false)
    expect(wrapper.findAll('button').some((item) => item.text().includes('修改申报'))).toBe(false)
    const viewButton = wrapper.findAll('button').find((item) => item.text().includes('查看 PDF 与证据'))
    await viewButton!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="evidence-editor"]').text()).toContain('只读')
    expect(wrapper.find('.title-edit').exists()).toBe(false)
  })

})
