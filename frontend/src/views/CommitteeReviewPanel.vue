<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { declarationApi, type Credentials, type Declaration, type Membership } from '@/api/declarations'
import { workflowApi, type QueueItem, type ReasonCode } from '@/api/workflow'
import EvidenceEditor from '@/views/EvidenceEditor.vue'

const props = defineProps<{ credentials: Credentials; memberships: Membership[] }>()
const committees = computed(() => props.memberships.filter((item) => item.role === 'CLASS_COMMITTEE'))
const classId = ref<number | undefined>(committees.value[0]?.classId)
const queue = ref<QueueItem[]>([])
const selected = ref<QueueItem>()
const declaration = ref<Declaration>()
const reasonCode = ref<ReasonCode>()
const customReason = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')

const reasonOptions: { value: ReasonCode; label: string }[] = [
  { value: 'ACTIVITY_INVALID', label: '活动不符合要求' },
  { value: 'EVIDENCE_INVALID', label: '证明材料无效' },
  { value: 'IDENTITY_NOT_FOUND', label: '未找到本人身份信息' },
  { value: 'OTHER', label: '其他' },
]

async function loadQueue() {
  if (!classId.value) return
  busy.value = true
  error.value = ''
  try {
    queue.value = await workflowApi.queue(props.credentials, classId.value)
    selected.value = undefined
    declaration.value = undefined
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '待审核列表加载失败'
  } finally {
    busy.value = false
  }
}

async function open(item: QueueItem) {
  busy.value = true
  error.value = ''
  try {
    const current = await declarationApi.get(props.credentials, item.id)
    if (current.status !== 'PENDING' || current.submissionVersion !== item.submissionVersion) {
      await loadQueue()
      error.value = '待审核列表已变化，请选择最新申报。'
      return
    }
    declaration.value = current
    selected.value = item
    reasonCode.value = undefined
    customReason.value = ''
  } catch (reason) {
    await loadQueue()
    error.value = reason instanceof Error
      ? '待审核列表已变化或申报加载失败：' + reason.message
      : '待审核列表已变化或申报加载失败，请刷新后重试'
  } finally {
    busy.value = false
  }
}

async function decide(result: 'APPROVED' | 'REJECTED') {
  if (!selected.value || !declaration.value) return
  if (result === 'REJECTED' && (!reasonCode.value || (reasonCode.value === 'OTHER' && !customReason.value.trim()))) {
    error.value = '请选择驳回原因；选择“其他”时需要填写具体原因。'
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await workflowApi.decide(props.credentials, selected.value.id, {
      submissionVersion: declaration.value.submissionVersion,
      result,
      ...(result === 'REJECTED' ? {
        reasonCode: reasonCode.value,
        ...(reasonCode.value === 'OTHER' ? { customReason: customReason.value.trim() } : {}),
      } : {}),
    })
    notice.value = result === 'APPROVED' ? '审核已通过。' : '已驳回申报。'
    await loadQueue()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '审核失败，请刷新待审核列表'
  } finally {
    busy.value = false
  }
}

onMounted(() => { void loadQueue() })
</script>

<template>
  <el-card shadow="never" class="form-card committee-panel">
    <template #header>班委审核</template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert v-if="notice" :title="notice" type="success" :closable="false" />
    <div class="committee-controls">
      <el-select v-model="classId" aria-label="审核班级" @change="loadQueue">
        <el-option
          v-for="membership in committees"
          :key="membership.classId"
          :label="membership.className"
          :value="membership.classId"
        />
      </el-select>
      <el-button :loading="busy" @click="loadQueue">刷新待审核列表</el-button>
    </div>
    <el-empty v-if="queue.length === 0" description="暂无待审核申报" />
    <div v-for="item in queue" :key="item.id" class="declaration-row">
      <div>
        <strong>{{ item.title }}</strong>
        <p>{{ item.ownerDisplayName }}（{{ item.ownerUsername }}）· {{ item.className }} · 第 {{ item.submissionVersion }} 次提交</p>
      </div>
      <el-button :disabled="busy" @click="open(item)">查看材料并审核</el-button>
    </div>

    <template v-if="selected && declaration">
      <EvidenceEditor
        :key="selected.id"
        :credentials="credentials"
        :declaration="declaration"
        read-only
        @updated="declaration = $event"
      />
      <div class="review-actions">
        <h3>审核第 {{ declaration.submissionVersion }} 次提交</h3>
        <el-button type="success" :loading="busy" @click="decide('APPROVED')">审核通过</el-button>
        <div class="reject-form">
          <el-select v-model="reasonCode" placeholder="选择驳回原因" aria-label="驳回原因">
            <el-option v-for="reason in reasonOptions" :key="reason.value" :label="reason.label" :value="reason.value" />
          </el-select>
          <el-input
            v-if="reasonCode === 'OTHER'"
            v-model="customReason"
            type="textarea"
            maxlength="500"
            show-word-limit
            placeholder="填写具体驳回原因"
          />
          <el-button type="danger" :loading="busy" @click="decide('REJECTED')">驳回申报</el-button>
        </div>
      </div>
    </template>
  </el-card>
</template>
