<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { PlusOutlined, UploadOutlined, FileOutlined, FileExcelOutlined, DownOutlined } from '@ant-design/icons-vue'
import { evaluationApi, downloadAuthenticated, uploadAuthenticated } from '@/apis/local_features'

// ---------- 测试集 ----------
const suitesLoading = ref(false)
const suites = ref([])
const suiteTotal = ref(0)
const suitePage = ref(1)
const suitePageSize = ref(10)
const suiteFilters = reactive({ keyword: '', category: '' })

const loadSuites = async () => {
  suitesLoading.value = true
  try {
    const res = await evaluationApi.suites({
      keyword: suiteFilters.keyword,
      category: suiteFilters.category,
      page: suitePage.value,
      page_size: suitePageSize.value,
    })
    if (res && res.status === 'success' && res.data) {
      suites.value = res.data.items || []
      suiteTotal.value = res.data.total || 0
    }
  } catch (e) {
    message.error('测试集加载失败: ' + (e.message || '未知错误'))
  } finally {
    suitesLoading.value = false
  }
}

const suiteColumns = [
  { title: '名称', dataIndex: 'name', key: 'name' },
  { title: '分类', dataIndex: 'category', key: 'category', width: 120 },
  { title: '用例数', dataIndex: 'case_count', key: 'case_count', width: 90 },
  { title: '创建人', dataIndex: 'created_by', key: 'created_by', width: 120 },
  { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 180 },
  { title: '操作', key: 'action', width: 260 },
]

// ---------- 测试集 新建/编辑 ----------
const suiteModalVisible = ref(false)
const suiteSaving = ref(false)
const editingSuite = ref(null)
const suiteForm = reactive({ name: '', description: '', category: '' })

const openSuiteCreate = () => {
  editingSuite.value = null
  Object.assign(suiteForm, { name: '', description: '', category: '' })
  suiteModalVisible.value = true
}

const openSuiteEdit = (record) => {
  editingSuite.value = record
  Object.assign(suiteForm, {
    name: record.name || '',
    description: record.description || '',
    category: record.category || '',
  })
  suiteModalVisible.value = true
}

const saveSuite = async () => {
  if (!suiteForm.name || !suiteForm.name.trim()) {
    message.warning('请填写测试集名称')
    return
  }
  suiteSaving.value = true
  try {
    const payload = {
      name: suiteForm.name.trim(),
      description: suiteForm.description || null,
      category: suiteForm.category || null,
    }
    if (editingSuite.value) {
      await evaluationApi.updateSuite(editingSuite.value.id, payload)
      message.success('测试集已更新')
    } else {
      await evaluationApi.createSuite(payload)
      message.success('测试集已创建')
    }
    suiteModalVisible.value = false
    await loadSuites()
  } catch (e) {
    message.error(e.message || '保存失败')
  } finally {
    suiteSaving.value = false
  }
}

const deleteSuite = (record) => {
  Modal.confirm({
    title: '删除测试集',
    content: `确定删除「${record.name}」？其中 ${record.case_count || 0} 条用例将一并删除，不可恢复。`,
    okType: 'danger',
    async onOk() {
      try {
        await evaluationApi.deleteSuite(record.id)
        message.success('已删除')
        if (currentSuite.value && currentSuite.value.id === record.id) {
          currentSuite.value = null
          caseDrawerOpen.value = false
        }
        await loadSuites()
      } catch (e) {
        message.error(e.message || '删除失败')
      }
    },
  })
}

// ---------- 用例管理抽屉 ----------
const caseDrawerOpen = ref(false)
const currentSuite = ref(null)
const casesLoading = ref(false)
const cases = ref([])
const caseTotal = ref(0)
const casePage = ref(1)
const casePageSize = ref(10)
const caseKeyword = ref('')

const openCases = (record) => {
  currentSuite.value = record
  caseKeyword.value = ''
  casePage.value = 1
  caseDrawerOpen.value = true
  loadCases()
}

const loadCases = async () => {
  if (!currentSuite.value) return
  casesLoading.value = true
  try {
    const res = await evaluationApi.cases(currentSuite.value.id, {
      keyword: caseKeyword.value,
      page: casePage.value,
      page_size: casePageSize.value,
    })
    if (res && res.status === 'success' && res.data) {
      cases.value = res.data.items || []
      caseTotal.value = res.data.total || 0
    }
  } catch (e) {
    message.error('用例加载失败: ' + (e.message || '未知错误'))
  } finally {
    casesLoading.value = false
  }
}

const diffColor = (d) => {
  if (d === 'easy') return 'green'
  if (d === 'medium') return 'orange'
  if (d === 'hard') return 'red'
  return 'default'
}

const diffLabel = (d) => ({ easy: '简单', medium: '中等', hard: '困难' }[d] || d || '-')

const caseColumns = [
  { title: '问题', dataIndex: 'question', key: 'question' },
  { title: '难度', dataIndex: 'difficulty', key: 'difficulty', width: 90 },
  { title: '分类', dataIndex: 'category', key: 'category', width: 110 },
  { title: '启用', dataIndex: 'enabled', key: 'enabled', width: 70 },
  { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 170 },
  { title: '操作', key: 'action', width: 130 },
]

// ---------- 用例 新建/编辑抽屉 ----------
const caseEditVisible = ref(false)
const caseSaving = ref(false)
const editingCase = ref(null)
const caseForm = reactive({
  question: '',
  answer: '',
  key_points: [],
  kb_id: '',
  category: '',
  difficulty: 'medium',
  enabled: true,
  note: '',
})

const openCaseCreate = () => {
  editingCase.value = null
  Object.assign(caseForm, {
    question: '', answer: '', key_points: [], kb_id: '', category: '',
    difficulty: 'medium', enabled: true, note: '',
  })
  caseEditVisible.value = true
}

const openCaseEdit = (record) => {
  editingCase.value = record
  Object.assign(caseForm, {
    question: record.question || '',
    answer: record.answer || '',
    key_points: record.key_points || [],
    kb_id: record.kb_id || '',
    category: record.category || '',
    difficulty: record.difficulty || 'medium',
    enabled: !!record.enabled,
    note: record.note || '',
  })
  caseEditVisible.value = true
}

const saveCase = async () => {
  if (!caseForm.question || !caseForm.question.trim()) {
    message.warning('问题不能为空')
    return
  }
  caseSaving.value = true
  try {
    const payload = {
      question: caseForm.question.trim(),
      answer: caseForm.answer || null,
      key_points: caseForm.key_points,
      kb_id: caseForm.kb_id || null,
      category: caseForm.category || null,
      difficulty: caseForm.difficulty,
      enabled: !!caseForm.enabled,
      note: caseForm.note || null,
    }
    if (editingCase.value) {
      await evaluationApi.updateCase(currentSuite.value.id, editingCase.value.id, payload)
      message.success('用例已更新')
    } else {
      await evaluationApi.createCase(currentSuite.value.id, payload)
      message.success('用例已添加')
    }
    caseEditVisible.value = false
    await loadCases()
  } catch (e) {
    message.error(e.message || '保存失败')
  } finally {
    caseSaving.value = false
  }
}

const deleteCase = (record) => {
  Modal.confirm({
    title: '删除用例',
    content: '确定删除该条用例？',
    okType: 'danger',
    async onOk() {
      try {
        await evaluationApi.deleteCase(currentSuite.value.id, record.id)
        message.success('已删除')
        await loadCases()
      } catch (e) {
        message.error(e.message || '删除失败')
      }
    },
  })
}

// ---------- 导入 ----------
const importModalVisible = ref(false)
const importFormat = ref('json')
const importing = ref(false)
const importResult = ref(null) // { imported, row_errors, total }
const importFile = ref(null)

const openImport = () => {
  importFormat.value = 'json'
  importResult.value = null
  importFile.value = null
  importModalVisible.value = true
}

const onImportFileChange = (info) => {
  importFile.value = info.file
}

const doImport = async () => {
  if (!importFile.value) {
    message.warning('请选择文件')
    return
  }
  importing.value = true
  try {
    const res = await uploadAuthenticated(
      evaluationApi.importUrl(currentSuite.value.id, importFormat.value),
      importFile.value,
    )
    if (res && res.status === 'success' && res.data) {
      importResult.value = res.data
      if (res.data.row_errors && res.data.row_errors.length) {
        message.warning(`导入失败：${res.data.row_errors.length} 行校验未通过`)
      } else {
        message.success(`成功导入 ${res.data.imported || 0} 条`)
      }
      await loadCases()
      if (currentSuite.value) {
        // 用例数变化，刷新测试集列表
        await loadSuites()
      }
    }
  } catch (e) {
    message.error(e.message || '导入失败')
  } finally {
    importing.value = false
  }
}

const closeImport = () => {
  if (!importing.value) importModalVisible.value = false
}

// ---------- 导出 ----------
const exportSuite = async (record, format) => {
  const ext = format === 'csv' ? 'csv' : 'json'
  const res = await downloadAuthenticated(
    evaluationApi.exportUrl(record.id, format),
    `suite_${record.id}.${ext}`,
  )
  if (!res.ok) {
    message.error(res.message || '导出失败')
  }
}

// ---------- 执行 ----------
const executeVisible = ref(false)
const executeLoading = ref(false)
const executeResult = ref(null)
const executingSuiteName = ref('')

const runSuite = async (record) => {
  executeLoading.value = true
  executeResult.value = null
  executingSuiteName.value = record.name
  try {
    const res = await evaluationApi.executeSuite(record.id)
    if (res && res.status === 'success' && res.data) {
      executeResult.value = res.data
      executeVisible.value = true
      const { total, passed, failed, errored } = res.data
      if (total > 0 && failed === 0 && errored === 0) {
        message.success(`执行完成：${passed}/${total} 通过`)
      } else {
        message.warning(`执行完成：通过 ${passed}，未通过 ${failed}，异常 ${errored}`)
      }
    }
  } catch (e) {
    message.error(e.message || '执行失败')
  } finally {
    executeLoading.value = false
  }
}

onMounted(loadSuites)
</script>

<template>
  <div class="evaluation-view">
    <div class="evaluation-toolbar">
      <a-input
        v-model:value="suiteFilters.keyword"
        placeholder="按名称/描述搜索"
        allow-clear
        style="width: 220px"
        @press-enter="() => { suitePage = 1; loadSuites() }"
      />
      <a-input
        v-model:value="suiteFilters.category"
        placeholder="分类"
        allow-clear
        style="width: 140px"
        @press-enter="() => { suitePage = 1; loadSuites() }"
      />
      <a-button type="primary" @click="() => { suitePage = 1; loadSuites() }">查询</a-button>
      <a-button @click="() => { suiteFilters.keyword = ''; suiteFilters.category = ''; suitePage = 1; loadSuites() }">重置</a-button>
      <a-divider type="vertical" />
      <a-button type="primary" @click="openSuiteCreate">
        <template #icon><PlusOutlined /></template>
        新建测试集
      </a-button>
    </div>

    <a-table
      :columns="suiteColumns"
      :data-source="suites"
      row-key="id"
      :loading="suitesLoading"
      size="middle"
      bordered
      :pagination="{
        current: suitePage,
        pageSize: suitePageSize,
        total: suiteTotal,
        showSizeChanger: true,
        showTotal: (t) => `共 ${t} 个测试集`,
        onChange: (page, size) => { suitePage = page; suitePageSize = size; loadSuites() },
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'">
          <a-button type="link" @click="openCases(record)">{{ record.name }}</a-button>
        </template>
        <template v-else-if="column.key === 'category'">
          <span v-if="record.category">{{ record.category }}</span>
          <span v-else class="muted">-</span>
        </template>
        <template v-else-if="column.key === 'action'">
          <a-button type="link" @click="openCases(record)">用例</a-button>
          <a-button type="link" :loading="executeLoading" @click="runSuite(record)">执行</a-button>
          <a-dropdown>
            <a-button type="link">导出<DownOutlined /></a-button>
            <template #overlay>
              <a-menu>
                <a-menu-item @click="exportSuite(record, 'json')">
                  <template #icon><FileOutlined /></template>导出 JSON
                </a-menu-item>
                <a-menu-item @click="exportSuite(record, 'csv')">
                  <template #icon><FileExcelOutlined /></template>导出 CSV
                </a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
          <a-button type="link" @click="openSuiteEdit(record)">编辑</a-button>
          <a-button type="link" danger @click="deleteSuite(record)">删除</a-button>
        </template>
      </template>
    </a-table>

    <!-- 测试集 新建/编辑 -->
    <a-modal
      v-model:open="suiteModalVisible"
      :title="editingSuite ? '编辑测试集' : '新建测试集'"
      :confirm-loading="suiteSaving"
      @ok="saveSuite"
    >
      <a-form :model="suiteForm" layout="vertical">
        <a-form-item label="名称" required>
          <a-input v-model:value="suiteForm.name" maxlength="255" placeholder="如：石油安全问答验收集" />
        </a-form-item>
        <a-form-item label="描述">
          <a-textarea v-model:value="suiteForm.description" :rows="3" />
        </a-form-item>
        <a-form-item label="分类">
          <a-input v-model:value="suiteForm.category" maxlength="50" placeholder="如：石油/安全/储运" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 用例管理抽屉 -->
    <a-drawer
      v-model:open="caseDrawerOpen"
      :title="`用例管理：${currentSuite?.name || ''}`"
      width="840px"
    >
      <div class="case-toolbar">
        <a-input
          v-model:value="caseKeyword"
          placeholder="按问题/答案搜索"
          allow-clear
          style="width: 200px"
          @press-enter="() => { casePage = 1; loadCases() }"
        />
        <a-button type="primary" @click="() => { casePage = 1; loadCases() }">查询</a-button>
        <a-divider type="vertical" />
        <a-button type="primary" @click="openCaseCreate">
          <template #icon><PlusOutlined /></template>
          添加用例
        </a-button>
        <a-button @click="openImport">
          <template #icon><UploadOutlined /></template>
          批量导入
        </a-button>
      </div>

      <a-table
        :columns="caseColumns"
        :data-source="cases"
        row-key="id"
        :loading="casesLoading"
        size="middle"
        :pagination="{
          current: casePage,
          pageSize: casePageSize,
          total: caseTotal,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条用例`,
          onChange: (page, size) => { casePage = page; casePageSize = size; loadCases() },
        }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'difficulty'">
            <a-tag :color="diffColor(record.difficulty)">{{ diffLabel(record.difficulty) }}</a-tag>
          </template>
          <template v-else-if="column.key === 'enabled'">
            <a-tag :color="record.enabled ? 'green' : 'default'">{{ record.enabled ? '启用' : '停用' }}</a-tag>
          </template>
          <template v-else-if="column.key === 'action'">
            <a-button type="link" @click="openCaseEdit(record)">编辑</a-button>
            <a-button type="link" danger @click="deleteCase(record)">删除</a-button>
          </template>
        </template>
      </a-table>
    </a-drawer>

    <!-- 用例 新建/编辑抽屉 -->
    <a-drawer
      v-model:open="caseEditVisible"
      :title="editingCase ? '编辑用例' : '添加用例'"
      width="520px"
      :footer-style="{ textAlign: 'right' }"
    >
      <a-form :model="caseForm" layout="vertical">
        <a-form-item label="问题" required>
          <a-textarea v-model:value="caseForm.question" :rows="2" placeholder="必填" />
        </a-form-item>
        <a-form-item label="标准答案">
          <a-textarea v-model:value="caseForm.answer" :rows="3" />
        </a-form-item>
        <a-form-item label="关键要点">
          <a-select v-model:value="caseForm.key_points" mode="tags" placeholder="输入后回车添加" />
        </a-form-item>
        <a-form-item label="知识库 ID">
          <a-input v-model:value="caseForm.kb_id" placeholder="如：kb_xxx" />
        </a-form-item>
        <a-form-item label="分类">
          <a-input v-model:value="caseForm.category" />
        </a-form-item>
        <a-form-item label="难度">
          <a-radio-group v-model:value="caseForm.difficulty">
            <a-radio value="easy">简单</a-radio>
            <a-radio value="medium">中等</a-radio>
            <a-radio value="hard">困难</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="启用">
          <a-switch v-model:checked="caseForm.enabled" checked-children="启用" un-checked-children="停用" />
        </a-form-item>
        <a-form-item label="备注">
          <a-input v-model:value="caseForm.note" />
        </a-form-item>
      </a-form>
      <template #footer>
        <a-button style="margin-right: 8px" @click="caseEditVisible = false">取消</a-button>
        <a-button type="primary" :loading="caseSaving" @click="saveCase">保存</a-button>
      </template>
    </a-drawer>

    <!-- 批量导入 -->
    <a-modal
      v-model:open="importModalVisible"
      title="批量导入用例"
      :confirm-loading="importing"
      :ok-text="importResult ? '继续导入' : '导入'"
      ok-type="primary"
      @ok="doImport"
      @cancel="closeImport"
    >
      <a-form layout="vertical">
        <a-form-item label="格式">
          <a-radio-group v-model:value="importFormat">
            <a-radio value="json">JSON</a-radio>
            <a-radio value="csv">CSV</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="文件（JSON 为用例数组或 {cases:[...]}；CSV 首行为表头 question,answer,key_points,kb_id,category,difficulty,enabled,note）">
          <a-upload :before-upload="() => false" :show-upload-list="true" :max-count="1" @change="onImportFileChange">
            <a-button>
              <template #icon><UploadOutlined /></template>
              选择文件
            </a-button>
          </a-upload>
        </a-form-item>
      </a-form>

      <a-alert
        v-if="importResult"
        :type="importResult.row_errors && importResult.row_errors.length ? 'error' : 'success'"
        :message="`共 ${importResult.total || 0} 行，成功 ${importResult.imported || 0} 行，失败 ${(importResult.row_errors || []).length} 行`"
        style="margin-top: 12px"
      />
      <a-table
        v-if="importResult && importResult.row_errors && importResult.row_errors.length"
        :data-source="importResult.row_errors"
        :columns="[{ title: '行号', dataIndex: 'row', width: 70 }, { title: '错误', dataIndex: 'error' }]"
        row-key="row"
        size="small"
        :pagination="false"
        style="margin-top: 8px"
      />
    </a-modal>

    <!-- 执行结果 -->
    <a-modal
      v-model:open="executeVisible"
      :title="`执行结果：${executingSuiteName}`"
      :footer="null"
      width="860px"
    >
      <div v-if="executeResult" class="execute-summary">
        <a-statistic title="总用例" :value="executeResult.total || 0" />
        <a-statistic title="通过" :value="executeResult.passed || 0" :value-style="{ color: '#52c41a' }" />
        <a-statistic title="未通过" :value="executeResult.failed || 0" :value-style="{ color: '#ff4d4f' }" />
        <a-statistic title="异常" :value="executeResult.errored || 0" :value-style="{ color: '#faad14' }" />
        <a-statistic title="未判分" :value="executeResult.unjudged || 0" />
      </div>
      <a-table
        v-if="executeResult"
        :data-source="executeResult.cases || []"
        row-key="case_id"
        size="small"
        :pagination="{ pageSize: 8 }"
        :columns="[
          { title: '问题', dataIndex: 'question', key: 'question' },
          { title: '模型回答', dataIndex: 'response', key: 'response', ellipsis: true },
          { title: '要点', dataIndex: 'key_points', key: 'key_points', width: 120 },
          { title: '判定', dataIndex: 'judged', key: 'judged', width: 90 },
          { title: '异常', dataIndex: 'error', key: 'error', width: 140 },
        ]"
        :scroll="{ x: 720 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'response'">
            <span v-if="record.response">{{ record.response }}</span>
            <span v-else class="muted">-</span>
          </template>
          <template v-else-if="column.key === 'key_points'">
            <span v-if="record.key_points && record.key_points.length">
              {{ record.key_points.join('、') }}
            </span>
            <span v-else class="muted">-</span>
          </template>
          <template v-else-if="column.key === 'judged'">
            <a-tag v-if="record.error" color="orange">异常</a-tag>
            <a-tag v-else-if="record.judged && record.matched" color="green">通过</a-tag>
            <a-tag v-else-if="record.judged && !record.matched" color="red">未通过</a-tag>
            <a-tag v-else color="default">未判分</a-tag>
          </template>
          <template v-else-if="column.key === 'error'">
            <span v-if="record.error">{{ record.error }}</span>
            <span v-else class="muted">-</span>
          </template>
        </template>
      </a-table>
    </a-modal>
  </div>
</template>

<style scoped>
.evaluation-view {
  padding: 16px;
}
.evaluation-toolbar,
.case-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.muted {
  color: rgba(0, 0, 0, 0.45);
}
.execute-summary {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
</style>
