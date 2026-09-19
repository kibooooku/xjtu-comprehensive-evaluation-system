<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type RenderTask,
} from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.mjs?url'

import { declarationApi, type Credentials, type Declaration } from '@/api/declarations'
import { evidenceApi, type EvidenceRegion, type EvidenceType } from '@/api/evidence'
import { normalizeDrag, toViewportRect, type NormalizedRect, type Point } from '@/evidence/coordinates'

GlobalWorkerOptions.workerSrc = workerUrl

const props = defineProps<{ credentials: Credentials; declaration: Declaration }>()
const emit = defineEmits<{ updated: [declaration: Declaration] }>()

const canvas = ref<HTMLCanvasElement>()
const overlay = ref<HTMLDivElement>()
const replacementInput = ref<HTMLInputElement>()
const document = ref<PDFDocumentProxy>()
const loadingTask = ref<PDFDocumentLoadingTask>()
const renderTask = ref<RenderTask>()
const pageNumber = ref(1)
const pageCount = ref(0)
const scale = ref(1)
const viewportWidth = ref(0)
const viewportHeight = ref(0)
const regions = ref<EvidenceRegion[]>([])
const selectedRect = ref<NormalizedRect>()
const selectedType = ref<EvidenceType>('IDENTITY')
const editingId = ref<number>()
const dragStart = ref<Point>()
const dragEnd = ref<Point>()
const busy = ref(false)
const error = ref('')
const notice = ref('')
let renderVersion = 0
let loadVersion = 0

function rectStyle(rect: NormalizedRect) {
  const box = toViewportRect(rect, viewportWidth.value, viewportHeight.value)
  return {
    left: `${box.left}px`,
    top: `${box.top}px`,
    width: `${box.width}px`,
    height: `${box.height}px`,
  }
}

function currentDrag(): NormalizedRect | undefined {
  if (!dragStart.value || !dragEnd.value || viewportWidth.value <= 0) return undefined
  return normalizeDrag(dragStart.value, dragEnd.value, viewportWidth.value, viewportHeight.value)
}

function pointFromPointer(event: PointerEvent): Point {
  const bounds = overlay.value!.getBoundingClientRect()
  return { x: event.clientX - bounds.left, y: event.clientY - bounds.top }
}

function startDrag(event: PointerEvent) {
  if (busy.value || !document.value) return
  dragStart.value = pointFromPointer(event)
  dragEnd.value = dragStart.value
  overlay.value?.setPointerCapture(event.pointerId)
}

function moveDrag(event: PointerEvent) {
  if (!dragStart.value) return
  dragEnd.value = pointFromPointer(event)
}

function endDrag(event: PointerEvent) {
  if (!dragStart.value) return
  dragEnd.value = pointFromPointer(event)
  const rect = currentDrag()
  if (rect && rect.width >= 0.002 && rect.height >= 0.002) {
    selectedRect.value = rect
    notice.value = '已框选区域，请选择证据类型并保存。'
  }
  dragStart.value = undefined
  dragEnd.value = undefined
}

function changePage(next: number) {
  clearSelection()
  pageNumber.value = next
}
function editRegion(region: EvidenceRegion) {
  pageNumber.value = region.pageNumber
  selectedRect.value = {
    x: region.x, y: region.y, width: region.width, height: region.height,
  }
  selectedType.value = region.type
  editingId.value = region.id
  notice.value = '可修改类型，或在页面上重新框选后保存。'
}

function clearSelection() {
  selectedRect.value = undefined
  editingId.value = undefined
}

async function saveRegion() {
  if (!selectedRect.value || !document.value) return
  busy.value = true
  error.value = ''
  try {
    const input = { ...selectedRect.value, type: selectedType.value, pageNumber: pageNumber.value }
    if (editingId.value) {
      const saved = await evidenceApi.update(props.credentials, props.declaration.id, editingId.value, props.declaration.pdf!.documentVersion, input)
      regions.value = regions.value.map((region) => region.id === saved.id ? saved : region)
    } else {
      const saved = await evidenceApi.create(props.credentials, props.declaration.id, props.declaration.pdf!.documentVersion, input)
      regions.value = [...regions.value, saved]
    }
    clearSelection()
    notice.value = '证据区域已保存。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    busy.value = false
  }
}

async function deleteRegion(region: EvidenceRegion) {
  busy.value = true
  error.value = ''
  try {
    await evidenceApi.delete(props.credentials, props.declaration.id, region.id, props.declaration.pdf!.documentVersion)
    regions.value = regions.value.filter((item) => item.id !== region.id)
    if (editingId.value === region.id) clearSelection()
    notice.value = '证据区域已删除。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '删除失败'
  } finally {
    busy.value = false
  }
}

async function renderPage() {
  const pdf = document.value
  const target = canvas.value
  if (!pdf || !target) return
  const version = ++renderVersion
  renderTask.value?.cancel()
  const page = await pdf.getPage(pageNumber.value)
  if (version !== renderVersion) return
  const viewport = page.getViewport({ scale: scale.value, rotation: 0 })
  viewportWidth.value = viewport.width
  viewportHeight.value = viewport.height
  const ratio = window.devicePixelRatio || 1
  target.width = Math.ceil(viewport.width * ratio)
  target.height = Math.ceil(viewport.height * ratio)
  target.style.width = `${viewport.width}px`
  target.style.height = `${viewport.height}px`
  const context = target.getContext('2d')
  if (!context) throw new Error('无法创建 PDF Canvas')
  const task = page.render({
    canvas: target,
    canvasContext: context,
    viewport,
    transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
  })
  renderTask.value = task
  try {
    await task.promise
  } catch (reason) {
    if (version === renderVersion && (reason as Error).name !== 'RenderingCancelledException') throw reason
  }
}

async function disposeDocument() {
  renderVersion++
  renderTask.value?.cancel()
  renderTask.value = undefined
  await loadingTask.value?.destroy()
  loadingTask.value = undefined
  document.value = undefined
}

async function loadDocument() {
  const version = ++loadVersion
  busy.value = true
  error.value = ''
  try {
    await disposeDocument()
    pageCount.value = 0
    viewportWidth.value = 0
    viewportHeight.value = 0
    regions.value = []
    clearSelection()
    const before = await declarationApi.get(props.credentials, props.declaration.id)
    const [blob, savedRegions] = await Promise.all([
      declarationApi.pdf(props.credentials, props.declaration.id),
      evidenceApi.list(props.credentials, props.declaration.id),
    ])
    if (version !== loadVersion) return
    const after = await declarationApi.get(props.credentials, props.declaration.id)
    if (before.pdf?.documentVersion !== after.pdf?.documentVersion) {
      emit('updated', after)
      throw new Error('PDF 在加载过程中已被替换，请重新加载。')
    }
    emit('updated', after)
    const task = getDocument({ data: new Uint8Array(await blob.arrayBuffer()) })
    loadingTask.value = task
    const pdf = await task.promise
    if (version !== loadVersion) {
      await task.destroy()
      return
    }
    document.value = pdf
    pageCount.value = pdf.numPages
    pageNumber.value = 1
    regions.value = savedRegions
    clearSelection()
    await nextTick()
    await renderPage()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'PDF 加载失败'
  } finally {
    busy.value = false
  }
}

async function replacePdf(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  input.value = ''
  if (!window.confirm('替换证明文件会清除已有证据标注，需要重新标注。确定替换吗？')) return
  busy.value = true
  error.value = ''
  try {
    const saved = await declarationApi.replacePdf(props.credentials, props.declaration.id, file)
    emit('updated', saved)
    regions.value = []
    clearSelection()
    notice.value = 'PDF 已替换，旧标注已清除。'
    await loadDocument()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'PDF 替换失败'
  } finally {
    busy.value = false
  }
}

watch([pageNumber, scale], () => {
  if (document.value) void renderPage().catch((reason) => {
    error.value = reason instanceof Error ? reason.message : '页面渲染失败'
  })
})

onMounted(() => { void loadDocument() })
onBeforeUnmount(() => { loadVersion++; void disposeDocument() })
</script>

<template>
  <section class="evidence-editor">
    <div class="evidence-toolbar">
      <strong>{{ declaration.title }} · 证据标注</strong>
      <div class="evidence-controls">
        <el-button :disabled="busy || pageNumber <= 1" @click="changePage(pageNumber - 1)">上一页</el-button>
        <span>{{ pageNumber }} / {{ pageCount || '…' }}</span>
        <el-button :disabled="busy || pageNumber >= pageCount" @click="changePage(pageNumber + 1)">下一页</el-button>
        <el-button :disabled="busy || scale <= 0.5" @click="scale = Math.max(0.5, scale - 0.25)">缩小</el-button>
        <span>{{ Math.round(scale * 100) }}%</span>
        <el-button :disabled="busy || scale >= 2.5" @click="scale = Math.min(2.5, scale + 0.25)">放大</el-button>
      </div>
    </div>
    <p>在 PDF 上拖拽矩形，然后选择“身份信息”或“材料有效性”并保存。选择已有区域后可重新框选。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-button v-if="error" @click="loadDocument">重新加载 PDF</el-button>
    <el-alert v-if="notice" :title="notice" type="success" :closable="false" />

    <div class="pdf-scroll">
      <div class="pdf-page" :style="{ width: `${viewportWidth}px`, height: `${viewportHeight}px` }">
        <canvas ref="canvas" />
        <div
          ref="overlay"
          class="pdf-overlay"
          aria-label="PDF 证据框选层"
          @pointerdown="startDrag"
          @pointermove="moveDrag"
          @pointerup="endDrag"
          @pointercancel="endDrag"
        >
          <button
            v-for="region in regions.filter((item) => item.pageNumber === pageNumber)"
            :key="region.id"
            type="button"
            class="evidence-box"
            :class="{ active: editingId === region.id }"
            :style="rectStyle(region)"
            :aria-label="`编辑${region.type === 'IDENTITY' ? '身份信息' : '材料有效性'}证据区域`"
            @pointerdown.stop
            @click.stop="editRegion(region)"
          />
          <div v-if="selectedRect" class="selection-box" :style="rectStyle(selectedRect)" />
          <div v-if="currentDrag()" class="selection-box" :style="rectStyle(currentDrag()!)" />
        </div>
      </div>
    </div>

    <div class="evidence-actions">
      <el-select v-model="selectedType" aria-label="证据类型" :disabled="!selectedRect || busy">
        <el-option label="身份信息（IDENTITY）" value="IDENTITY" />
        <el-option label="材料有效性（VALIDITY）" value="VALIDITY" />
      </el-select>
      <el-button type="primary" :disabled="!document || !selectedRect || busy" @click="saveRegion">
        {{ editingId ? '更新区域' : '保存区域' }}
      </el-button>
      <el-button :disabled="!selectedRect || busy" @click="clearSelection">取消选择</el-button>
    </div>

    <div class="region-list">
      <strong>已保存区域（{{ regions.length }}）</strong>
      <div v-for="region in regions" :key="region.id" class="region-row">
        <span>第 {{ region.pageNumber }} 页 · {{ region.type === 'IDENTITY' ? '身份信息' : '材料有效性' }} · {{ region.source }}</span>
        <div>
          <el-button size="small" :disabled="busy" @click="editRegion(region)">编辑/重新框选</el-button>
          <el-button size="small" type="danger" plain :disabled="busy" @click="deleteRegion(region)">删除</el-button>
        </div>
      </div>
    </div>

    <div class="replace-panel">
      <strong>替换证明文件</strong>
      <p>替换证明文件会清除已有证据标注，需要重新标注。</p>
      <input ref="replacementInput" type="file" accept="application/pdf,.pdf" :disabled="busy" @change="replacePdf" />
    </div>
  </section>
</template>
