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
}))

vi.mock('@/api/declarations', () => ({ declarationApi: api }))

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
  pdf: null,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
}

const saved = {
  ...draft,
  hasPdf: true,
  pdf: { originalFilename: 'evidence.pdf', sizeBytes: 12, sha256: 'fictional-sha' },
}

describe('DeclarationCreateView', () => {
  const createObjectUrl = vi.fn()
  const revokeObjectUrl = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    createObjectUrl.mockReturnValueOnce('blob:first').mockReturnValueOnce('blob:second')
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectUrl })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectUrl })
    api.me.mockResolvedValue(profile)
    api.mine.mockResolvedValue([])
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
    expect(wrapper.text()).toContain('暂无草稿')
  })

  it('creates before uploading and previews authenticated Blob while revoking old URLs', async () => {
    const callOrder: string[] = []
    api.create.mockImplementation(async () => {
      callOrder.push('create')
      return draft
    })
    api.uploadPdf.mockImplementation(async () => {
      callOrder.push('upload')
      return saved
    })
    api.pdf.mockImplementation(async () => {
      callOrder.push('pdf')
      return new Blob(['%PDF-1.4'], { type: 'application/pdf' })
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
    expect(callOrder).toEqual(['create', 'upload', 'pdf'])
    expect(api.create).toHaveBeenCalledWith(credentials, 10, '虚构竞赛证明')
    expect(api.uploadPdf).toHaveBeenCalledWith(credentials, 42, file)
    expect(api.pdf).toHaveBeenCalledWith(credentials, 42)
    expect((input.element as HTMLInputElement).value).toBe('')
    expect(createObjectUrl).toHaveBeenCalledTimes(1)
    expect(wrapper.get('object').attributes('data')).toBe('blob:first')

    const viewButton = wrapper.findAll('button').find((button) =>
      button.text().includes('重新查看本人 PDF'),
    )
    expect(viewButton).toBeDefined()
    await viewButton!.trigger('click')
    await flushPromises()

    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:first')
    expect(createObjectUrl).toHaveBeenCalledTimes(2)
    expect(wrapper.get('object').attributes('data')).toBe('blob:second')

    wrapper.unmount()
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:second')
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

})
