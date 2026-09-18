<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

import {
  declarationApi,
  type Credentials,
  type Declaration,
  type Me,
} from '@/api/declarations'

const username = ref('')
const password = ref('')
const credentials = ref<Credentials | null>(null)
const me = ref<Me | null>(null)
const declarations = ref<Declaration[]>([])
const classId = ref<number>()
const title = ref('')
const pdfFile = ref<File>()
const fileInput = ref<HTMLInputElement>()
const busy = ref(false)
const error = ref('')
const notice = ref('')
const previewUrl = ref('')
const previewTitle = ref('')

const canSubmit = computed(
  () => Boolean(credentials.value && classId.value && title.value.trim() && pdfFile.value),
)

function clearPreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  previewTitle.value = ''
}

async function login() {
  error.value = ''
  notice.value = ''
  clearPreview()
  busy.value = true
  const next = { username: username.value.trim(), password: password.value }
  try {
    const [profile, mine] = await Promise.all([
      declarationApi.me(next),
      declarationApi.mine(next),
    ])
    credentials.value = next
    me.value = profile
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

function choosePdf(event: Event) {
  const input = event.target as HTMLInputElement
  pdfFile.value = input.files?.[0]
}

function replaceDeclaration(saved: Declaration) {
  declarations.value = declarations.value.map((item) => (item.id === saved.id ? saved : item))
}

function resetFileInput() {
  pdfFile.value = undefined
  if (fileInput.value) fileInput.value.value = ''
}

async function uploadToDraft(draft: Declaration) {
  if (!credentials.value || !pdfFile.value) return
  error.value = ''
  notice.value = ''
  busy.value = true
  try {
    const saved = await declarationApi.uploadPdf(credentials.value, draft.id, pdfFile.value)
    replaceDeclaration(saved)
    resetFileInput()
    notice.value = 'PDF 已保存到草稿。'
    await viewPdf(saved)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'PDF 上传失败，可选择文件后重试此草稿'
  } finally {
    busy.value = false
  }
}

async function createDeclaration() {
  if (!credentials.value || !classId.value || !pdfFile.value) return
  error.value = ''
  notice.value = ''
  busy.value = true
  try {
    const draft = await declarationApi.create(credentials.value, classId.value, title.value.trim())
    declarations.value = [draft, ...declarations.value]
    title.value = ''
    try {
      const saved = await declarationApi.uploadPdf(credentials.value, draft.id, pdfFile.value)
      replaceDeclaration(saved)
      resetFileInput()
      notice.value = '草稿申报及 PDF 已保存。'
      await viewPdf(saved)
    } catch (reason) {
      error.value = reason instanceof Error
        ? '草稿已创建，但 PDF 上传失败：' + reason.message + '。可在草稿列表中重试。'
        : '草稿已创建，但 PDF 上传失败，可在草稿列表中重试。'
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '申报创建失败'
  } finally {
    busy.value = false
  }
}

async function viewPdf(item: Declaration) {
  if (!credentials.value) return
  error.value = ''
  busy.value = true
  clearPreview()
  try {
    const blob = await declarationApi.pdf(credentials.value, item.id)
    previewUrl.value = URL.createObjectURL(blob)
    previewTitle.value = item.pdf?.originalFilename ?? item.title
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'PDF 获取失败'
  } finally {
    busy.value = false
  }
}

onBeforeUnmount(clearPreview)
</script>

<template>
  <main class="page-shell">
    <section class="hero">
      <p class="eyebrow">学生申报</p>
      <h1>创建草稿并保存 PDF</h1>
      <p class="summary">材料仅通过本人账号访问。本页不会生成公开文件链接。</p>
    </section>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <el-alert v-if="notice" :title="notice" type="success" show-icon :closable="false" />

    <el-card v-if="!me" shadow="never" class="form-card">
      <template #header>登录</template>
      <el-form label-position="top" @submit.prevent="login">
        <el-form-item label="用户名">
          <el-input v-model="username" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" autocomplete="current-password" show-password />
        </el-form-item>
        <el-button native-type="submit" type="primary" :loading="busy">登录并读取班级</el-button>
      </el-form>
    </el-card>

    <template v-else>
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
          <el-form-item label="申报标题">
            <el-input v-model="title" maxlength="200" show-word-limit />
          </el-form-item>
          <el-form-item label="证明材料 PDF">
            <input ref="fileInput" type="file" accept="application/pdf,.pdf" @change="choosePdf" />
          </el-form-item>
          <el-button native-type="submit" type="primary" :disabled="!canSubmit" :loading="busy">
            创建草稿并上传
          </el-button>
        </el-form>
      </el-card>

      <el-card shadow="never" class="form-card">
        <template #header>我的草稿</template>
        <el-empty v-if="declarations.length === 0" description="暂无草稿" />
        <div v-for="item in declarations" :key="item.id" class="declaration-row">
          <div>
            <strong>{{ item.title }}</strong>
            <p>{{ item.className }} · {{ item.status }} · {{ item.pdf?.originalFilename ?? '未上传 PDF' }}</p>
          </div>
          <el-button v-if="item.hasPdf" :disabled="busy" @click="viewPdf(item)">重新查看本人 PDF</el-button>
          <el-button
            v-else
            type="primary"
            plain
            :disabled="busy || !pdfFile"
            @click="uploadToDraft(item)"
          >
            上传所选 PDF 到此草稿
          </el-button>
        </div>
      </el-card>

      <el-card v-if="previewUrl" shadow="never" class="preview-card">
        <template #header>{{ previewTitle }}</template>
        <object :data="previewUrl" type="application/pdf" aria-label="本人 PDF 预览">
          当前浏览器无法内嵌预览 PDF。
        </object>
      </el-card>
    </template>
  </main>
</template>
