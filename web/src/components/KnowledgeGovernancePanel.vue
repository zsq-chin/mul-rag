<template>
  <div class="governance-panel">
    <div class="governance-toolbar">
      <a-input
        v-model:value="filters.keyword"
        placeholder="按文件名搜索"
        allow-clear
        style="width: 220px"
        @press-enter="handleSearch"
      />
      <a-select
        v-model:value="filters.knowledge_type"
        placeholder="知识类型"
        allow-clear
        style="width: 140px"
        :options="knowledgeTypeOptions"
        @change="handleSearch"
      />
      <a-select
        v-model:value="filters.confidentiality"
        placeholder="密级"
        allow-clear
        style="width: 120px"
        :options="confidentialityOptions"
        @change="handleSearch"
      />
      <a-button @click="handleSearch">查询</a-button>
      <a-button @click="resetFilters">重置</a-button>
      <a-divider type="vertical" />
      <a-button type="primary" @click="handleSync" :loading="syncing">同步治理元数据</a-button>
      <a-button @click="handleExport" :loading="exporting">导出 XLSX</a-button>
    </div>

    <a-table
      :columns="columns"
      :data-source="items"
      row-key="file_id"
      :loading="loading"
      size="middle"
      bordered
      :pagination="pagination"
      class="governance-table"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'filename'">
          <a-button type="link" @click="handlePreview(record)">{{ record.filename }}</a-button>
        </template>
        <template v-else-if="column.key === 'knowledge_type'">
          <span v-if="record.knowledge_type">{{ record.knowledge_type }}</span>
          <span v-else class="muted">-</span>
        </template>
        <template v-else-if="column.key === 'confidentiality'">
          <a-tag :color="confTagColor(record.confidentiality)">
            {{ confLabel(record.confidentiality) }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'download_allowed'">
          <a-tag :color="record.download_allowed ? 'green' : 'red'">
            {{ record.download_allowed ? '允许' : '禁止' }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'usage_count'">
          <span>{{ record.usage_count ?? 0 }}</span>
        </template>
        <template v-else-if="column.key === 'action'">
          <a-button type="link" @click="handleEdit(record)">编辑</a-button>
          <a-button type="link" @click="handleDownload(record)">下载</a-button>
        </template>
      </template>
    </a-table>

    <!-- 治理字段编辑 -->
    <a-modal
      v-model:open="editVisible"
      title="编辑治理信息"
      :confirm-loading="editSaving"
      @ok="handleEditSave"
    >
      <a-form :model="editForm" layout="vertical">
        <a-form-item label="专业领域" name="domain">
          <a-input v-model:value="editForm.domain" placeholder="如：石油储运" />
        </a-form-item>
        <a-form-item label="知识类型" name="knowledge_type">
          <a-select
            v-model:value="editForm.knowledge_type"
            allow-clear
            :options="knowledgeTypeOptions"
            placeholder="报告/论文/设计图/日志/标准/其他"
          />
        </a-form-item>
        <a-form-item label="密级" name="confidentiality">
          <a-radio-group v-model:value="editForm.confidentiality">
            <a-radio value="public">公开</a-radio>
            <a-radio value="internal">内部</a-radio>
            <a-radio value="restricted">受控</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="标签" name="tags">
          <a-select v-model:value="editForm.tags" mode="tags" placeholder="输入后回车添加" />
        </a-form-item>
        <a-form-item label="允许下载" name="download_allowed">
          <a-switch v-model:checked="editForm.download_allowed" checked-children="允许" un-checked-children="禁止" />
        </a-form-item>
        <a-form-item label="责任部门" name="owner_department">
          <a-input v-model:value="editForm.owner_department" />
        </a-form-item>
        <a-form-item label="来源更新时间" name="source_updated_at">
          <a-date-picker v-model:value="editForm.source_updated_at" show-time style="width: 100%" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 文档预览（元数据，不含正文） -->
    <a-modal
      v-model:open="previewVisible"
      :title="preview?.filename || '文档信息'"
      :footer="null"
    >
      <a-descriptions v-if="preview" :column="1" size="small" bordered>
        <a-descriptions-item label="文件 ID">{{ preview.file_id }}</a-descriptions-item>
        <a-descriptions-item label="文件类型">{{ preview.file_type }}</a-descriptions-item>
        <a-descriptions-item label="状态">{{ preview.status }}</a-descriptions-item>
        <a-descriptions-item label="节点数">{{ preview.node_count ?? 0 }}</a-descriptions-item>
        <a-descriptions-item label="密级">{{ confLabel(preview.confidentiality) }}</a-descriptions-item>
        <a-descriptions-item label="知识类型">{{ preview.knowledge_type || '-' }}</a-descriptions-item>
        <a-descriptions-item label="标签">{{ (preview.tags || []).join('，') || '-' }}</a-descriptions-item>
        <a-descriptions-item label="允许下载">{{ preview.download_allowed ? '允许' : '禁止' }}</a-descriptions-item>
        <a-descriptions-item label="使用次数">{{ preview.usage_count ?? 0 }}</a-descriptions-item>
        <a-descriptions-item label="责任部门">{{ preview.owner_department || '-' }}</a-descriptions-item>
        <a-descriptions-item label="来源更新时间">{{ preview.source_updated_at || '-' }}</a-descriptions-item>
      </a-descriptions>
    </a-modal>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { governanceApi, downloadAuthenticated } from '@/apis/local_features'

const props = defineProps({
  dbId: {
    type: String,
    default: '',
  },
})

const loading = ref(false)
const syncing = ref(false)
const exporting = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const filters = reactive({
  keyword: '',
  knowledge_type: undefined,
  confidentiality: undefined,
})

const knowledgeTypeOptions = ['报告', '论文', '设计图', '日志', '标准', '其他'].map((v) => ({
  label: v,
  value: v,
}))
const confidentialityOptions = [
  { label: '公开', value: 'public' },
  { label: '内部', value: 'internal' },
  { label: '受控', value: 'restricted' },
]

const columns = [
  { title: '文件名', key: 'filename', dataIndex: 'filename' },
  { title: '类型', key: 'knowledge_type', dataIndex: 'knowledge_type', width: 90 },
  { title: '密级', key: 'confidentiality', dataIndex: 'confidentiality', width: 80 },
  { title: '允许下载', key: 'download_allowed', dataIndex: 'download_allowed', width: 90 },
  { title: '使用次数', key: 'usage_count', dataIndex: 'usage_count', width: 90 },
  { title: '来源更新时间', key: 'source_updated_at', dataIndex: 'source_updated_at', width: 160 },
  { title: '操作', key: 'action', width: 130 },
]

const pagination = reactive({
  current: 1,
  pageSize,
  total,
  showSizeChanger: true,
  showTotal: (t) => `共 ${t} 条`,
  onChange: (p, ps) => {
    page.value = p
    pageSize.value = ps
    load()
  },
  onShowSizeChange: (p, ps) => {
    page.value = p
    pageSize.value = ps
    load()
  },
})

function confLabel(value) {
  return value === 'restricted' ? '受控' : value === 'public' ? '公开' : '内部'
}
function confTagColor(value) {
  return value === 'restricted' ? 'red' : value === 'public' ? 'green' : 'blue'
}

async function load() {
  loading.value = true
  try {
    const res = await governanceApi.list(props.dbId, {
      keyword: filters.keyword,
      knowledge_type: filters.knowledge_type,
      confidentiality: filters.confidentiality,
      page: page.value,
      page_size: pageSize.value,
    })
    items.value = res.data?.items || []
    total.value = res.data?.total || 0
  } catch (e) {
    message.error(e.message || '加载治理信息失败')
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  page.value = 1
  pagination.current = 1
  load()
}

function resetFilters() {
  filters.keyword = ''
  filters.knowledge_type = undefined
  filters.confidentiality = undefined
  handleSearch()
}

// --- 编辑治理字段 ---
const editVisible = ref(false)
const editSaving = ref(false)
const editForm = reactive({
  file_id: '',
  domain: undefined,
  knowledge_type: undefined,
  confidentiality: 'internal',
  tags: [],
  download_allowed: true,
  owner_department: undefined,
  source_updated_at: undefined,
})

function handleEdit(record) {
  Object.assign(editForm, {
    file_id: record.file_id,
    domain: record.domain,
    knowledge_type: record.knowledge_type,
    confidentiality: record.confidentiality || 'internal',
    tags: record.tags || [],
    download_allowed: !!record.download_allowed,
    owner_department: record.owner_department,
    source_updated_at: record.source_updated_at || undefined,
  })
  editVisible.value = true
}

async function handleEditSave() {
  editSaving.value = true
  try {
    await governanceApi.update(props.dbId, editForm.file_id, {
      domain: editForm.domain || null,
      knowledge_type: editForm.knowledge_type || null,
      confidentiality: editForm.confidentiality,
      tags: editForm.tags,
      download_allowed: editForm.download_allowed,
      owner_department: editForm.owner_department || null,
      source_updated_at: editForm.source_updated_at || null,
    })
    message.success('治理信息已更新')
    editVisible.value = false
    load()
  } catch (e) {
    message.error(e.message || '更新失败')
  } finally {
    editSaving.value = false
  }
}

// --- 预览 ---
const previewVisible = ref(false)
const preview = ref(null)

async function handlePreview(record) {
  try {
    const res = await governanceApi.get(props.dbId, record.file_id)
    preview.value = res.data
    previewVisible.value = true
  } catch (e) {
    message.error(e.message || '预览失败')
  }
}

// --- 受控下载 ---
async function handleDownload(record) {
  const result = await downloadAuthenticated(
    governanceApi.downloadUrl(props.dbId, record.file_id),
    record.filename,
  )
  if (result.ok) {
    message.success('下载已开始')
    load()
  } else {
    message.error(result.message || '下载被拒绝')
  }
}

// --- 同步 / 导出 ---
async function handleSync() {
  syncing.value = true
  try {
    const res = await governanceApi.sync(props.dbId)
    message.success(
      `已同步：新增 ${res.data?.created ?? 0} 条，共 ${res.data?.total ?? 0} 条`,
    )
    load()
  } catch (e) {
    message.error(e.message || '同步失败')
  } finally {
    syncing.value = false
  }
}

async function handleExport() {
  exporting.value = true
  try {
    const result = await downloadAuthenticated(
      governanceApi.exportUrl(props.dbId, 'xlsx'),
      `governance_${props.dbId}.xlsx`,
    )
    if (result.ok) {
      message.success('导出已开始')
    } else {
      message.error(result.message || '导出失败')
    }
  } finally {
    exporting.value = false
  }
}

onMounted(load)
</script>

<style lang="less" scoped>
.governance-panel {
  width: 100%;

  .governance-toolbar {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: 12px;
  }

  .muted {
    color: var(--color-text-secondary, #999);
  }
}
</style>
