<script setup lang="ts">
const modules = [
  '学生结构化申报与材料管理',
  'PDF 文字层提取与 OCR 候选定位',
  '确定性评分规则与人工审核',
  '班级汇总与模板化导出',
]
</script>

<template>
  <main class="page-shell">
    <section class="hero" aria-labelledby="page-title">
      <p class="eyebrow">工程骨架已就绪</p>
      <h1 id="page-title">西交班级综合素质测评管理系统</h1>
      <p class="summary">
        当前版本只包含可构建的前端基础设施。OCR 负责识别文字和坐标，评分由确定性规则完成，材料有效性由班委人工审核。
      </p>
      <el-tag type="success" effect="plain">Bootstrap 0.1.0</el-tag>
    </section>

    <section class="module-grid" aria-label="规划模块">
      <el-card v-for="module in modules" :key="module" shadow="never">
        {{ module }}
      </el-card>
    </section>
  </main>
</template>
