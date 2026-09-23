<script setup lang="ts">
import { computed, ref } from 'vue'

import { declarationApi, type Credentials, type Declaration, type Me } from '@/api/declarations'
import { workflowApi } from '@/api/workflow'
import CommitteeReviewPanel from '@/views/CommitteeReviewPanel.vue'
import EvidenceEditor from '@/views/EvidenceEditor.vue'
import ScoreItemsPanel from '@/views/ScoreItemsPanel.vue'

const username = ref('')
const password = ref('')
const credentials = ref<Credentials | null>(null)
const me = ref<Me | null>(null)
const studentNumber = ref('')
const studentName = ref('')
const declarations = ref<Declaration[]>([])
const classId = ref<number>()
const title = ref('')
const editTitle = ref('')
const pdfFile = ref<File>()
const fileInput = ref<HTMLInputElement>()
const busy = ref(false)
const error = ref('')
const notice = ref('')
const selectedDeclarationId = ref<number>()
const selectedDeclaration = computed(() =>
  declarations.value.find((item) => item.id === selectedDeclarationId.value),
)
const canSubmitNew = computed(() =>
  Boolean(credentials.value && classId.value && title.value.trim() && pdfFile.value),
)
const reasonLabels: Record<string, string> = {
  ACTIVITY_INVALID: '活动不符合要求',
  EVIDENCE_INVALID: '证明材料无效',
  IDENTITY_NOT_FOUND: '未找到本人身份信息',
  OTHER: '其他原因',
}

function rejectionText(item: Declaration): string {
  if (item.status !== 'REJECTED' || !item.latestReasonCode) return ''
  const label = reasonLabels[item.latestReasonCode] ?? item.latestReasonCode
  return item.latestCustomReason ? label + '：' + item.latestCustomReason : label
}

function replaceDeclaration(saved: Declaration) {
  declarations.value = declarations.value.map((item) => item.id === saved.id ? saved : item)
}

function resetFileInput() {
  pdfFile.value = undefined
  if (fileInput.value) fileInput.value.value = ''
}

async function login() {
  error.value = ''
  notice.value = ''
  selectedDeclarationId.value = undefined
  busy.value = true
  const next = { username: username.value.trim(), password: password.value }
  try {
    const [profile, mine] = await Promise.all([
      declarationApi.me(next),
      declarationApi.mine(next),
    ])
    credentials.value = next
    me.value = profile
    studentNumber.value = profile.studentNumber ?? ''
    studentName.value = profile.studentName ?? ''
    declarations.value = mine
    classId.value = profile.memberships[0]?.classId
  } catch (reason) {
    credentials.value = null
    me.value = null
    error.value = reason instanceof Error ? reason.message : '登录失败'
  } finally {
    busy.value = false
  }
}

async function saveIdentity() {
  if (!credentials.value || !studentNumber.value.trim() || !studentName.value.trim()) return
  busy.value = true
  error.value = ''
  try {
    me.value = await declarationApi.updateIdentity(
      credentials.value, studentNumber.value.trim(), studentName.value.trim(),
    )
    notice.value = '身份信息已保存，PDF 身份候选区域将按新信息重新查找。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '身份信息保存失败'
  } finally {
    busy.value = false
  }
}

function choosePdf(event: Event) {
  pdfFile.value = (event.target as HTMLInputElement).files?.[0]
}

async function uploadToDraft(draft: Declaration) {
  if (!credentials.value || !pdfFile.value) return
  busy.value = true
  error.value = ''
  try {
    const saved = await declarationApi.uploadPdf(credentials.value, draft.id, pdfFile.value)
    replaceDeclaration(saved)
    resetFileInput()
    openDeclaration(saved)
    notice.value = 'PDF 已保存到草稿。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'PDF 上传失败，可在草稿中重试'
  } finally {
    busy.value = false
  }
}

async function createDeclaration() {
  if (!credentials.value || !classId.value || !pdfFile.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const draft = await declarationApi.create(credentials.value, classId.value, title.value.trim())
    declarations.value = [draft, ...declarations.value]
    title.value = ''
    try {
      const saved = await declarationApi.uploadPdf(credentials.value, draft.id, pdfFile.value)
      replaceDeclaration(saved)
      resetFileInput()
      openDeclaration(saved)
      notice.value = '草稿及 PDF 已保存。'
    } catch (reason) {
      error.value = reason instanceof Error
        ? '草稿已创建，但 PDF 上传失败：' + reason.message + '。可在列表中重试。'
        : '草稿已创建，但 PDF 上传失败，可在列表中重试。'
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '申报创建失败'
  } finally {
    busy.value = false
  }
}

function openDeclaration(item: Declaration) {
  selectedDeclarationId.value = item.id
  editTitle.value = item.title
}

async function saveTitle() {
  if (!credentials.value || !selectedDeclaration.value || selectedDeclaration.value.status !== 'DRAFT') return
  busy.value = true
  error.value = ''
  try {
    const saved = await declarationApi.updateTitle(credentials.value, selectedDeclaration.value.id, editTitle.value)
    replaceDeclaration(saved)
    notice.value = '标题已保存。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '标题保存失败'
  } finally {
    busy.value = false
  }
}

async function submit(item: Declaration) {
  if (!credentials.value || !item.pdf) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await workflowApi.submit(credentials.value, item.id, item.pdf.documentVersion)
    replaceDeclaration(await declarationApi.get(credentials.value, item.id))
    notice.value = '申报已正式提交，等待本班班委审核。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '提交失败'
  } finally {
    busy.value = false
  }
}

async function revise(item: Declaration) {
  if (!credentials.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await workflowApi.revise(credentials.value, item.id, item.submissionVersion)
    const refreshed = await declarationApi.get(credentials.value, item.id)
    replaceDeclaration(refreshed)
    openDeclaration(refreshed)
    notice.value = '申报已返回草稿，可以修改后重新提交。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法进入修改状态'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="page-shell">
    <section class="hero">
      <p class="eyebrow">学生申报与班委审核</p>
      <h1>班级综合素质测评申报</h1>
      <p class="summary">草稿标注完成后正式提交；班委只审核本班已提交的申报。</p>
    </section>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <el-alert v-if="notice" :title="notice" type="success" show-icon :closable="false" />

    <el-card v-if="!me" shadow="never" class="form-card">
      <template #header>登录</template>
      <el-form label-position="top" @submit.prevent="login">
        <el-form-item label="用户名"><el-input v-model="username" autocomplete="username" /></el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" autocomplete="current-password" show-password />
        </el-form-item>
        <el-button native-type="submit" type="primary" :loading="busy">登录并读取班级</el-button>
      </el-form>
    </el-card>

    <template v-else>
      <el-card shadow="never" class="form-card">
        <template #header>身份信息（用于查找 PDF 文本）</template>
        <el-form label-position="top" @submit.prevent="saveIdentity">
          <el-form-item label="学号"><el-input v-model="studentNumber" maxlength="32" /></el-form-item>
          <el-form-item label="姓名"><el-input v-model="studentName" maxlength="100" /></el-form-item>
          <el-button native-type="submit" :disabled="busy || !studentNumber.trim() || !studentName.trim()" :loading="busy">
            保存身份信息
          </el-button>
        </el-form>
      </el-card>

      <el-card shadow="never" class="form-card">
        <template #header>{{ me.displayName }} · 新建草稿</template>
        <el-form label-position="top" @submit.prevent="createDeclaration">
          <el-form-item label="所属班级">
            <el-select v-model="classId" placeholder="选择班级" style="width: 100%">
              <el-option
                v-for="membership in me.memberships"
                :key="membership.classId"
                :label="`${membership.className}（${membership.role === 'STUDENT' ? '学生' : '班委'}）`"
                :value="membership.classId"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="申报标题"><el-input v-model="title" maxlength="200" show-word-limit /></el-form-item>
          <el-form-item label="证明材料 PDF">
            <input ref="fileInput" type="file" accept="application/pdf,.pdf" @change="choosePdf" />
          </el-form-item>
          <el-button native-type="submit" type="primary" :disabled="!canSubmitNew" :loading="busy">
            创建草稿并上传
          </el-button>
        </el-form>
      </el-card>

      <el-card shadow="never" class="form-card">
        <template #header>我的申报</template>
        <el-empty v-if="declarations.length === 0" description="暂无申报" />
        <div v-for="item in declarations" :key="item.id" class="declaration-row">
          <div>
            <strong>{{ item.title }}</strong>
            <p>{{ item.className }} · {{ item.status }} · 第 {{ item.submissionVersion }} 次提交 · {{ item.pdf?.originalFilename ?? '未上传 PDF' }}</p>
            <p v-if="item.status === 'REJECTED'">最近驳回原因：{{ rejectionText(item) }}</p>
          </div>
          <div class="declaration-actions">
            <el-button v-if="item.hasPdf" :disabled="busy" @click="openDeclaration(item)">查看 PDF 与证据</el-button>
            <el-button v-if="item.status === 'DRAFT' && item.hasPdf" type="primary" :disabled="busy" @click="submit(item)">正式提交</el-button>
            <el-button v-if="item.status === 'REJECTED'" type="warning" :disabled="busy" @click="revise(item)">修改申报</el-button>
            <el-button
              v-if="item.status === 'DRAFT' && !item.hasPdf"
              type="primary" plain :disabled="busy || !pdfFile" @click="uploadToDraft(item)"
            >上传所选 PDF 到此草稿</el-button>
          </div>
        </div>
      </el-card>

      <el-card v-if="selectedDeclaration && credentials" shadow="never" class="preview-card">
        <div v-if="selectedDeclaration.status === 'DRAFT'" class="title-edit">
          <el-input v-model="editTitle" maxlength="200" aria-label="修改申报标题" />
          <el-button :disabled="busy || !editTitle.trim()" @click="saveTitle">保存标题</el-button>
        </div>
        <ScoreItemsPanel
          :key="'score-' + selectedDeclaration.id + '-' + selectedDeclaration.status + '-' + selectedDeclaration.submissionVersion"
          :credentials="credentials" :declaration="selectedDeclaration" :read-only="selectedDeclaration.status !== 'DRAFT'"
        />
        <EvidenceEditor
          :key="selectedDeclaration.id + '-' + selectedDeclaration.status + '-' + selectedDeclaration.submissionVersion + '-' + (me.studentNumber ?? '') + '-' + (me.studentName ?? '')"
          :credentials="credentials"
          :declaration="selectedDeclaration"
          :read-only="selectedDeclaration.status !== 'DRAFT'"
          @updated="replaceDeclaration"
        />
      </el-card>

      <CommitteeReviewPanel
        v-if="credentials && me.memberships.some((item) => item.role === 'CLASS_COMMITTEE')"
        :credentials="credentials"
        :memberships="me.memberships"
      />
    </template>
  </main>
</template>
