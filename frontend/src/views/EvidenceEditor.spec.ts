import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EvidenceEditor from './EvidenceEditor.vue'

const mocks = vi.hoisted(() => ({
  pdf: vi.fn(),
  get: vi.fn(),
  replacePdf: vi.fn(),
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  delete: vi.fn(),
  getDocument: vi.fn(),
}))
vi.mock('@/api/declarations', () => ({ declarationApi: { pdf: mocks.pdf, get: mocks.get, replacePdf: mocks.replacePdf } }))
vi.mock('@/api/evidence', () => ({ evidenceApi: {
  list: mocks.list, create: mocks.create, update: mocks.update, delete: mocks.delete,
} }))
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: mocks.getDocument,
}))
vi.mock('pdfjs-dist/build/pdf.worker.mjs?url', () => ({ default: 'mock-worker' }))

const credentials = { username: 'owner', password: 'test-password' }
const declaration = {
  id: 42, classId: 10, className: '虚构一班', title: '虚构证明',
  status: 'DRAFT' as const, hasPdf: true,
  pdf: { originalFilename: 'old.pdf', sizeBytes: 20, sha256: 'fictional', documentVersion: 1 },
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}
const region = {
  id: 7, documentId: 50, type: 'IDENTITY' as const, pageNumber: 1,
  x: 0.1, y: 0.2, width: 0.3, height: 0.4, source: 'MANUAL' as const,
  createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}

async function renderEditor() {
  const wrapper = mount(EvidenceEditor, {
    props: { credentials, declaration },
    global: { plugins: [ElementPlus] },
  })
  await flushPromises()
  return wrapper
}

function button(wrapper: ReturnType<typeof mount>, phrase: string) {
  const found = wrapper.findAll('button').find((item) => item.text().includes(phrase))
  expect(found).toBeDefined()
  return found!
}

describe('EvidenceEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as CanvasRenderingContext2D)
    mocks.pdf.mockResolvedValue({ arrayBuffer: async () => new Uint8Array([1, 2]).buffer })
    mocks.get.mockResolvedValue(declaration)
    mocks.list.mockResolvedValue([])
    mocks.getDocument.mockImplementation(() => ({
      promise: Promise.resolve({
        numPages: 2,
        getPage: async () => ({
          getViewport: ({ scale }: { scale: number }) => ({ width: 400 * scale, height: 200 * scale }),
          render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
        }),
      }),
      destroy: vi.fn().mockResolvedValue(undefined),
    }))
    vi.spyOn(window, 'confirm').mockReturnValue(false)
  })

  it('creates a normalized box, restores saved boxes, and deletes one', async () => {
    mocks.create.mockImplementation(async (_credentials, _id, _version, input) => ({ ...region, ...input }))
    mocks.delete.mockResolvedValue(undefined)
    const wrapper = await renderEditor()
    const overlay = wrapper.get('[aria-label="PDF 证据框选层"]')
    const element = overlay.element as HTMLElement
    element.setPointerCapture = vi.fn()
    element.getBoundingClientRect = () => ({
      left: 0, top: 0, right: 400, bottom: 200, width: 400, height: 200,
      x: 0, y: 0, toJSON: () => ({}),
    })
    for (const [type, x, y] of [['pointerdown', 40, 40], ['pointermove', 160, 120], ['pointerup', 160, 120]] as const) {
      const event = new Event(type, { bubbles: true })
      Object.defineProperties(event, {
        clientX: { value: x }, clientY: { value: y }, pointerId: { value: 1 },
      })
      element.dispatchEvent(event)
    }
    await flushPromises()
    await button(wrapper, '保存区域').trigger('click')
    await flushPromises()
    expect(mocks.create).toHaveBeenCalledTimes(1)
    const [usedCredentials, declarationId, version, input] = mocks.create.mock.calls[0]
    expect(usedCredentials).toEqual(credentials)
    expect(declarationId).toBe(42)
    expect(version).toBe(1)
    expect(input.type).toBe('IDENTITY')
    expect(input.pageNumber).toBe(1)
    expect(input.x).toBeCloseTo(0.1, 6)
    expect(input.y).toBeCloseTo(0.2, 6)
    expect(input.width).toBeCloseTo(0.3, 6)
    expect(input.height).toBeCloseTo(0.4, 6)
    expect(wrapper.findAll('.evidence-box')).toHaveLength(1)
    expect(wrapper.get('.evidence-box').attributes('style')).toContain('left: 40px')
    await button(wrapper, '删除').trigger('click')
    await flushPromises()
    expect(mocks.delete).toHaveBeenCalledWith(credentials, 42, 7, 1)
    expect(wrapper.findAll('.evidence-box')).toHaveLength(0)
    wrapper.unmount()

    mocks.list.mockResolvedValue([region])
    const restored = await renderEditor()
    expect(restored.findAll('.evidence-box')).toHaveLength(1)
    expect(restored.get('.evidence-box').attributes('style')).toContain('left: 40px')
    restored.unmount()
  })

  it('preserves a selected region when editing across pages', async () => {
    const secondPage = { ...region, pageNumber: 2 }
    mocks.list.mockResolvedValue([secondPage])
    mocks.update.mockImplementation(async (_credentials, _id, _regionId, _version, input) => ({
      ...secondPage, ...input,
    }))
    const wrapper = await renderEditor()
    await button(wrapper, '编辑/重新框选').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('2 / 2')
    expect(wrapper.get('.selection-box').exists()).toBe(true)
    expect(button(wrapper, '更新区域').attributes('disabled')).toBeUndefined()
    await button(wrapper, '更新区域').trigger('click')
    await flushPromises()
    expect(mocks.update).toHaveBeenCalledWith(credentials, 42, 7, 1, expect.objectContaining({
      pageNumber: 2, x: 0.1, y: 0.2,
    }))
    wrapper.unmount()
  })

  it('clears old regions and reloads PDF after confirmed replacement', async () => {
    mocks.list.mockResolvedValueOnce([region]).mockResolvedValueOnce([])
    const newDeclaration = { ...declaration, pdf: { ...declaration.pdf, documentVersion: 2, originalFilename: 'new.pdf' } }
    mocks.get.mockResolvedValueOnce(declaration).mockResolvedValueOnce(declaration)
      .mockResolvedValueOnce(newDeclaration).mockResolvedValueOnce(newDeclaration)
    mocks.replacePdf.mockResolvedValue({
      ...declaration, pdf: { ...declaration.pdf, documentVersion: 2, originalFilename: 'new.pdf' },
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = await renderEditor()
    const input = wrapper.get('.replace-panel input[type="file"]')
    const file = new File(['%PDF-1.4'], 'new.pdf', { type: 'application/pdf' })
    Object.defineProperty(input.element, 'files', { configurable: true, value: [file] })
    await input.trigger('change')
    await flushPromises()
    expect(mocks.replacePdf).toHaveBeenCalledWith(credentials, 42, file)
    expect(wrapper.emitted('updated')?.some(([item]) =>
      (item as typeof declaration).pdf?.documentVersion === 2
      && (item as typeof declaration).pdf?.originalFilename === 'new.pdf',
    )).toBe(true)
    expect(mocks.pdf).toHaveBeenCalledTimes(2)
    expect(mocks.list).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('.evidence-box')).toHaveLength(0)
    wrapper.unmount()
  })

  it('refuses mixed PDF and region data if the document changes during loading', async () => {
    const newer = { ...declaration, pdf: { ...declaration.pdf, documentVersion: 2, originalFilename: 'new.pdf' } }
    mocks.get.mockResolvedValueOnce(declaration).mockResolvedValueOnce(newer)
      .mockResolvedValueOnce(newer).mockResolvedValueOnce(newer)
    mocks.list.mockResolvedValueOnce([region]).mockResolvedValueOnce([])
    const wrapper = await renderEditor()

    expect(mocks.get).toHaveBeenCalledTimes(2)
    expect(mocks.pdf).toHaveBeenCalledTimes(1)
    expect(mocks.list).toHaveBeenCalledTimes(1)
    expect(mocks.getDocument).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('PDF 在加载过程中已被替换')
    expect(wrapper.text()).toContain('重新加载 PDF')
    expect(wrapper.text()).toContain('已保存区域（0）')
    expect(wrapper.findAll('.evidence-box')).toHaveLength(0)
    expect(wrapper.get('.pdf-page').attributes('style')).toContain('width: 0px')
    expect(wrapper.emitted('updated')?.[0]?.[0]).toMatchObject({
      pdf: { documentVersion: 2, originalFilename: 'new.pdf' },
    })

    await button(wrapper, '重新加载 PDF').trigger('click')
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledTimes(4)
    expect(mocks.getDocument).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).not.toContain('PDF 在加载过程中已被替换')
    wrapper.unmount()
  })

  it('drops an unsaved old selection after a concurrent replacement and requires a fresh box', async () => {
    const newer = { ...declaration, pdf: { ...declaration.pdf, documentVersion: 2, originalFilename: 'new.pdf' } }
    mocks.get.mockResolvedValueOnce(declaration).mockResolvedValueOnce(declaration)
      .mockResolvedValueOnce(declaration).mockResolvedValueOnce(newer)
      .mockResolvedValueOnce(newer).mockResolvedValueOnce(newer)
    mocks.create.mockRejectedValueOnce(new Error('PDF was replaced; reload before editing evidence'))
      .mockImplementationOnce(async (_credentials, _id, _version, input) => ({ ...region, ...input }))

    const wrapper = await renderEditor()
    const element = wrapper.get('[aria-label="PDF 证据框选层"]').element as HTMLElement
    element.setPointerCapture = vi.fn()
    element.getBoundingClientRect = () => ({
      left: 0, top: 0, right: 400, bottom: 200, width: 400, height: 200,
      x: 0, y: 0, toJSON: () => ({}),
    })
    const drag = async () => {
      for (const [type, x, y] of [['pointerdown', 40, 40], ['pointermove', 160, 120], ['pointerup', 160, 120]] as const) {
        const event = new Event(type, { bubbles: true })
        Object.defineProperties(event, {
          clientX: { value: x }, clientY: { value: y }, pointerId: { value: 1 },
        })
        element.dispatchEvent(event)
      }
      await flushPromises()
    }

    await drag()
    expect(wrapper.findAll('.selection-box')).toHaveLength(1)
    await button(wrapper, '保存区域').trigger('click')
    await flushPromises()
    expect(mocks.create).toHaveBeenCalledTimes(1)
    expect(mocks.create.mock.calls[0]?.[2]).toBe(1)

    await button(wrapper, '重新加载 PDF').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('PDF 在加载过程中已被替换')
    expect(wrapper.findAll('.selection-box')).toHaveLength(0)
    expect(button(wrapper, '保存区域').attributes('disabled')).toBeDefined()
    expect(mocks.create).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ declaration: newer })
    await button(wrapper, '重新加载 PDF').trigger('click')
    await flushPromises()
    expect(button(wrapper, '保存区域').attributes('disabled')).toBeDefined()
    expect(mocks.create).toHaveBeenCalledTimes(1)

    await drag()
    expect(button(wrapper, '保存区域').attributes('disabled')).toBeUndefined()
    await button(wrapper, '保存区域').trigger('click')
    await flushPromises()
    expect(mocks.create).toHaveBeenCalledTimes(2)
    expect(mocks.create.mock.calls[1]?.[2]).toBe(2)
    wrapper.unmount()
  })

  it('asks before replacement and preserves regions when cancelled', async () => {
    mocks.list.mockResolvedValue([region])
    const wrapper = await renderEditor()
    const input = wrapper.get('.replace-panel input[type="file"]')
    const file = new File(['%PDF-1.4'], 'new.pdf', { type: 'application/pdf' })
    Object.defineProperty(input.element, 'files', { configurable: true, value: [file] })
    await input.trigger('change')
    await flushPromises()
    expect(window.confirm).toHaveBeenCalled()
    expect(mocks.replacePdf).not.toHaveBeenCalled()
    expect(wrapper.findAll('.evidence-box')).toHaveLength(1)
    wrapper.unmount()
  })
})
