<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Credentials, Declaration } from '@/api/declarations'
import { scoreApi, type Award, type Category, type ItemType, type Level, type ScoreInput, type ScoreItem, type ScoreRule, type Subcategory } from '@/api/scores'
import { labels, optionsFor, previewScore } from '@/score/scoreOptions'

const props = defineProps<{ credentials: Credentials; declaration: Declaration; readOnly?: boolean }>()
const rules = ref<ScoreRule[]>([])
const items = ref<ScoreItem[]>([])
const activityName = ref('')
const category = ref<Category>()
const subcategory = ref<Subcategory>()
const itemType = ref<ItemType>()
const level = ref<Level>()
const award = ref<Award>()
const editingId = ref<number>()
const busy = ref(false)
const error = ref('')
const notice = ref('')
const canEdit = computed(() => !props.readOnly && props.declaration.status === 'DRAFT')
const choice = computed(() => ({ category: category.value, subcategory: subcategory.value,
  itemType: itemType.value, level: level.value, award: award.value }))
const categories = computed(() => optionsFor(rules.value, 'category', choice.value))
const subcategories = computed(() => optionsFor(rules.value, 'subcategory', choice.value))
const itemTypes = computed(() => optionsFor(rules.value, 'itemType', choice.value))
const levels = computed(() => optionsFor(rules.value, 'level', choice.value))
const awards = computed(() => optionsFor(rules.value, 'award', choice.value))
const suggestion = computed(() => previewScore(rules.value, choice.value))
const canSave = computed(() => canEdit.value && activityName.value.trim().length > 0 && suggestion.value !== null)

function resetBelow(field: 'category' | 'subcategory' | 'itemType' | 'level') {
  if (field === 'category') subcategory.value = undefined
  if (field === 'category' || field === 'subcategory') itemType.value = undefined
  if (field !== 'level') level.value = undefined
  award.value = undefined
}
function clearForm() {
  editingId.value = undefined
  activityName.value = ''
  category.value = undefined
  subcategory.value = undefined
  itemType.value = undefined
  level.value = undefined
  award.value = undefined
}
function edit(item: ScoreItem) {
  if (!canEdit.value) return
  editingId.value = item.id
  activityName.value = item.activityName
  category.value = item.category
  subcategory.value = item.subcategory
  itemType.value = item.itemType
  level.value = item.level
  award.value = item.award
}
async function load() {
  busy.value = true
  error.value = ''
  try {
    const [activeRules, savedItems] = await Promise.all([
      scoreApi.rules(props.credentials, props.declaration.classId), scoreApi.list(props.credentials, props.declaration.id),
    ])
    rules.value = activeRules
    items.value = savedItems
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加分条目加载失败'
  } finally { busy.value = false }
}
async function save() {
  if (!canSave.value || !category.value || !subcategory.value || !itemType.value || !level.value || !award.value) return
  const input: ScoreInput = { activityName: activityName.value.trim(), category: category.value,
    subcategory: subcategory.value, itemType: itemType.value, level: level.value, award: award.value }
  busy.value = true
  error.value = ''
  try {
    if (editingId.value) await scoreApi.update(props.credentials, props.declaration.id,
      editingId.value, props.declaration.submissionVersion, input)
    else await scoreApi.create(props.credentials, props.declaration.id,
      props.declaration.submissionVersion, input)
    items.value = await scoreApi.list(props.credentials, props.declaration.id)
    clearForm()
    notice.value = '加分条目已保存，正式提交时后端会重新计分。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally { busy.value = false }
}
async function remove(item: ScoreItem) {
  if (!canEdit.value) return
  busy.value = true
  error.value = ''
  try {
    await scoreApi.delete(props.credentials, props.declaration.id, item.id, props.declaration.submissionVersion)
    items.value = items.value.filter((saved) => saved.id !== item.id)
    if (editingId.value === item.id) clearForm()
    notice.value = '加分条目已删除。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '删除失败'
  } finally { busy.value = false }
}
onMounted(() => { void load() })
</script>

<template>
  <section class="score-panel">
    <h3>结构化加分条目</h3>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert v-if="notice" :title="notice" type="success" :closable="false" />
    <el-empty v-if="items.length === 0" description="暂无加分条目，正式提交前至少添加一项" />
    <div v-for="item in items" :key="item.id" class="declaration-row score-row">
      <div>
        <strong>{{ item.activityName }}</strong>
        <p>{{ labels[item.category] }} · {{ labels[item.subcategory] }} · {{ labels[item.itemType] }}</p>
        <p>{{ labels[item.level] }} · {{ labels[item.award] }} · 系统规则建议 {{ item.calculatedScore }} 分</p>
      </div>
      <div v-if="canEdit" class="declaration-actions">
        <el-button :disabled="busy" @click="edit(item)">编辑</el-button>
        <el-button type="danger" plain :disabled="busy" @click="remove(item)">删除</el-button>
      </div>
    </div>
    <div v-if="canEdit" class="score-form">
      <h4>{{ editingId ? '编辑加分条目' : '新增加分条目' }}</h4>
      <el-input v-model="activityName" maxlength="200" placeholder="活动名称" aria-label="活动名称" />
      <div class="score-selects">
        <el-select v-model="category" placeholder="类别" aria-label="类别" @change="resetBelow('category')">
          <el-option v-for="value in categories" :key="value" :label="labels[value]" :value="value" />
        </el-select>
        <el-select v-model="subcategory" placeholder="子类" aria-label="子类" :disabled="!category" @change="resetBelow('subcategory')">
          <el-option v-for="value in subcategories" :key="value" :label="labels[value]" :value="value" />
        </el-select>
        <el-select v-model="itemType" placeholder="项目类型" aria-label="项目类型" :disabled="!subcategory" @change="resetBelow('itemType')">
          <el-option v-for="value in itemTypes" :key="value" :label="labels[value]" :value="value" />
        </el-select>
        <el-select v-model="level" placeholder="级别" aria-label="级别" :disabled="!itemType" @change="resetBelow('level')">
          <el-option v-for="value in levels" :key="value" :label="labels[value]" :value="value" />
        </el-select>
        <el-select v-model="award" placeholder="奖项" aria-label="奖项" :disabled="!level">
          <el-option v-for="value in awards" :key="value" :label="labels[value]" :value="value" />
        </el-select>
      </div>
      <p>建议分数：{{ suggestion === null ? '请选择有规则的组合' : `${suggestion} 分` }}</p>
      <el-button type="primary" :disabled="!canSave || busy" @click="save">{{ editingId ? '保存修改' : '添加加分条目' }}</el-button>
      <el-button v-if="editingId" @click="clearForm">取消编辑</el-button>
    </div>
  </section>
</template>
