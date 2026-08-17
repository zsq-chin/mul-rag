<template>
  <div class="kd-view">
    <HeaderComponent title="知识字典" :loading="state.loading">
      <template #actions>
        <template v-if="isManager">
          <a-button type="primary" @click="openCreateModal">
            <template #icon><BookPlus :size="16" /></template>
            新建字典
          </a-button>
          <a-button @click="openWizard">
            <template #icon><Wand2 :size="16" /></template>
            生成知识字典
          </a-button>
          <a-button :loading="seedLoading" @click="handleSeedImport">
            <template #icon><Database :size="16" /></template>
            种子迁移
          </a-button>
        </template>
        <a-badge :count="activeJobCount" :overflow-count="99" :offset="[6, -4]">
          <a-button @click="openTaskPanel">
            <template #icon><ListTodo :size="16" /></template>
            任务面板
          </a-button>
        </a-badge>
      </template>
    </HeaderComponent>

    <div class="kd-body">
      <!-- 筛选栏 -->
      <div class="kd-toolbar">
        <a-input-search
          v-model:value="filters.keyword"
          placeholder="按名称关键字搜索"
          allow-clear
          style="width: 240px"
          @search="handleSearch"
        />
        <a-select
          v-model:value="filters.status"
          placeholder="发布状态"
          allow-clear
          style="width: 140px"
          :options="statusOptions"
          @change="handleSearch"
        />
        <a-input
          v-model:value="filters.domain"
          placeholder="专业领域"
          allow-clear
          style="width: 180px"
          @press-enter="handleSearch"
        />
        <a-button type="primary" @click="handleSearch">查询</a-button>
        <a-button @click="resetFilters">重置</a-button>
      </div>

      <!-- 字典列表 -->
      <a-table
        :columns="columns"
        :data-source="items"
        row-key="id"
        :loading="state.loading"
        :pagination="false"
        size="middle"
        bordered
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'">
            <a-tooltip :title="record.description || '暂无说明'">
              <a-button type="link" class="kd-name" @click="goDetail(record)">{{ record.name }}</a-button>
            </a-tooltip>
          </template>
          <template v-else-if="column.key === 'domain'">
            <span v-if="record.domain">{{ record.domain }}</span>
            <span v-else class="muted">-</span>
          </template>
          <template v-else-if="column.key === 'active_version'">
            <template v-if="record.active_version">
              <span class="v-no">V{{ record.active_version.version_no }}</span>
              <a-tag v-if="record.active_version.index_status" :color="indexColor(record.active_version.index_status)">
                {{ indexLabel(record.active_version.index_status) }}
              </a-tag>
            </template>
            <span v-else class="muted">-</span>
          </template>
          <template v-else-if="column.key === 'entry_count'">
            {{ record.active_version ? (record.active_version.entry_count ?? 0) : 0 }}
          </template>
          <template v-else-if="column.key === 'source_types'">
            <template v-if="record.source_types && record.source_types.length">
              <a-tag v-for="st in record.source_types" :key="st" :color="sourceTypeColor(st)">
                {{ sourceTypeLabel(st) }}
              </a-tag>
            </template>
            <span v-else class="muted">-</span>
          </template>
          <template v-else-if="column.key === 'status'">
            <a-tag :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
          </template>
          <template v-else-if="column.key === 'index_status'">
            <a-tag v-if="record.active_version && record.active_version.index_status" :color="indexColor(record.active_version.index_status)">
              {{ indexLabel(record.active_version.index_status) }}
            </a-tag>
            <span v-else class="muted">-</span>
          </template>
          <template v-else-if="column.key === 'updated_at'">
            {{ formatTime(record.updated_at) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <a-button type="link" @click="goDetail(record)">查看</a-button>
            <template v-if="isManager">
              <a-button type="link" @click="openEditModal(record)">编辑</a-button>
              <a-button type="link" danger @click="handleDelete(record)">删除</a-button>
            </template>
          </template>
        </template>
      </a-table>

      <div class="kd-pagination">
        <a-pagination
          v-model:current="page"
          v-model:page-size="pageSize"
          :total="total"
          show-size-changer
          show-total
          @change="onPageChange"
          @show-size-change="onSizeChange"
        />
      </div>
    </div>

    <!-- 新建字典 -->
    <a-modal v-model:open="createModal.open" title="新建字典" :confirm-loading="createModal.loading" @ok="submitCreate">
      <a-form :model="createForm" layout="vertical">
        <a-form-item label="名称" required>
          <a-input v-model:value="createForm.name" maxlength="255" placeholder="如：压裂知识字典" />
        </a-form-item>
        <a-form-item label="说明">
          <a-textarea v-model:value="createForm.description" :rows="3" maxlength="2000" placeholder="字典用途说明（可选）" />
        </a-form-item>
        <a-form-item label="专业领域">
          <a-input v-model:value="createForm.domain" maxlength="255" placeholder="如：石油压裂（可选）" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 编辑字典 -->
    <a-modal v-model:open="editModal.open" title="编辑字典" :confirm-loading="editModal.loading" @ok="submitEdit">
      <a-form :model="editForm" layout="vertical">
        <a-form-item label="名称" required>
          <a-input v-model:value="editForm.name" maxlength="255" />
        </a-form-item>
        <a-form-item label="说明">
          <a-textarea v-model:value="editForm.description" :rows="3" maxlength="2000" />
        </a-form-item>
        <a-form-item label="专业领域">
          <a-input v-model:value="editForm.domain" maxlength="255" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 生成向导 -->
    <a-modal
      v-model:open="wizardVisible"
      title="生成知识字典"
      width="760"
      :footer="null"
      :mask-closable="false"
      destroy-on-close
    >
      <a-steps :current="wizardStep" size="small" style="margin-bottom: 20px">
        <a-step title="选择来源" />
        <a-step title="配置生成" />
        <a-step title="确认任务" />
      </a-steps>

      <!-- 步骤 1：选择来源 -->
      <div v-if="wizardStep === 0" class="wizard-step">
        <a-radio-group v-model:value="sourceKind" class="wizard-source-kind">
          <a-radio value="kb_file">知识库文件</a-radio>
          <a-radio value="upload">上传文件</a-radio>
          <a-radio value="kb">整个知识库</a-radio>
        </a-radio-group>

        <!-- 知识库文件 -->
        <div v-if="sourceKind === 'kb_file'" class="wizard-source-body">
          <a-select
            v-model:value="sourceKbId"
            show-search
            placeholder="选择知识库"
            style="width: 340px"
            :options="kbOptions"
            :loading="kbLoading"
            option-filter-prop="label"
            @change="onSourceKbChange"
          />
          <div v-if="sourceKbId" class="kb-file-picker">
            <div class="kb-file-toolbar">
              <a-input-search
                v-model:value="kbFilesKeyword"
                placeholder="搜索文件名"
                allow-clear
                size="small"
                style="width: 220px"
                @search="onKbFileSearch"
              />
              <span class="muted">共 {{ kbFilesTotal }} 个文件，未解析文件不可选</span>
            </div>
            <a-table
              :data-source="kbFiles"
              :columns="fileColumns"
              row-key="file_id"
              size="small"
              :loading="kbFilesLoading"
              :pagination="false"
              :row-selection="{ type: 'radio', selectedRowKeys: fileSelectedKeys, onChange: onFileSelect, getCheckboxProps: fileCheckboxProps }"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'file_name'">
                  <span>{{ record.file_name }}</span>
                  <a-tag v-if="record.node_count === 0" color="orange" style="margin-left: 6px">未解析</a-tag>
                </template>
                <template v-else-if="column.key === 'node_count'">
                  {{ record.node_count ?? 0 }}
                </template>
              </template>
            </a-table>
            <a-pagination
              v-model:current="kbFilesPage"
              v-model:page-size="kbFilesPageSize"
              :total="kbFilesTotal"
              size="small"
              show-size-changer
              style="margin-top: 8px"
              @change="loadKbFiles"
              @show-size-change="onKbFileSizeChange"
            />
          </div>
        </div>

        <!-- 上传文件 -->
        <div v-if="sourceKind === 'upload'" class="wizard-source-body">
          <a-upload :before-upload="handleBeforeUpload" :show-upload-list="false" :disabled="uploadLoading">
            <a-button :loading="uploadLoading">
              <template #icon><Upload :size="16" /></template>
              选择文件上传
            </a-button>
          </a-upload>
          <div v-if="uploadFile" class="upload-file-info">
            <a-tag color="blue">{{ uploadFile.file_name }}</a-tag>
            <span class="muted">{{ formatSize(uploadFile.size_bytes) }}</span>
            <a-button type="link" size="small" @click="clearUpload">移除</a-button>
          </div>
          <p class="muted">
            支持 .pdf / .docx / .xlsx / .csv / .txt，大小不超过 100MB；文件仅用于本次生成，不会写入知识库。
          </p>
        </div>

        <!-- 整个知识库 -->
        <div v-if="sourceKind === 'kb'" class="wizard-source-body">
          <a-select
            v-model:value="sourceKbId"
            show-search
            placeholder="选择知识库"
            style="width: 340px"
            :options="kbOptions"
            :loading="kbLoading"
            option-filter-prop="label"
            @change="onSourceKbChange"
          />
          <template v-if="sourceKb">
            <div class="kb-stats">
              <a-statistic title="文件总数" :value="sourceKb.file_count || 0" />
              <a-statistic title="已解析" :value="sourceKb.parsed_count || 0" />
              <a-statistic title="异常" :value="sourceKb.error_count || 0" />
            </div>
            <a-alert
              v-if="sourceKb.error_count > 0 || sourceKb.parsed_count < sourceKb.file_count"
              type="warning"
              show-icon
              message="部分文件未解析，任务将跳过"
            />
          </template>
        </div>
      </div>

      <!-- 步骤 2：配置生成 -->
      <div v-if="wizardStep === 1" class="wizard-step">
        <a-form :model="wizardConfig" layout="vertical">
          <a-form-item label="字典">
            <a-select v-model:value="wizardConfig.dictionary_id" style="width: 340px" :options="wizardDictOptions" @change="onWizardDictChange" />
          </a-form-item>
          <a-form-item label="字典名称" required>
            <a-input
              v-model:value="wizardConfig.name"
              maxlength="255"
              :disabled="!!wizardConfig.dictionary_id"
              :placeholder="wizardConfig.dictionary_id ? '使用所选现有字典的名称' : '新建字典名称（必填）'"
            />
          </a-form-item>
          <a-form-item label="专业领域">
            <a-input v-model:value="wizardConfig.domain" maxlength="255" placeholder="如：石油压裂（可选）" />
          </a-form-item>
          <a-form-item label="说明">
            <a-textarea v-model:value="wizardConfig.description" :rows="2" maxlength="2000" placeholder="生成字典用途说明（可选）" />
          </a-form-item>
          <a-form-item label="生成模型">
            <a-select v-model:value="wizardConfig.model_id" style="width: 340px" :options="modelOptions" placeholder="系统默认模型" />
          </a-form-item>
          <a-form-item label="目标分类">
            <a-select v-model:value="wizardConfig.categories" mode="tags" placeholder="输入分类后回车，如：压裂、井控（可选）" />
          </a-form-item>
          <a-form-item label="使用种子字典">
            <a-switch v-model:checked="wizardConfig.use_seed" checked-children="使用" un-checked-children="不使用" />
          </a-form-item>
          <a-form-item label="重复处理策略">
            <a-select
              v-model:value="wizardConfig.duplicate_policy"
              disabled
              style="width: 240px"
              :options="[{ label: '确定性合并 (merge)', value: 'merge' }]"
            />
          </a-form-item>
        </a-form>
      </div>

      <!-- 步骤 3：确认任务 -->
      <div v-if="wizardStep === 2" class="wizard-step">
        <a-descriptions :column="1" size="small" bordered>
          <a-descriptions-item label="来源类型">{{ sourceKindLabel(sourceKind) }}</a-descriptions-item>
          <a-descriptions-item label="来源对象">{{ sourceSummaryText }}</a-descriptions-item>
          <a-descriptions-item label="预计文件数">{{ expectedFileCount }}</a-descriptions-item>
          <a-descriptions-item label="字典名称">{{ finalDictName }}</a-descriptions-item>
          <a-descriptions-item label="专业领域">{{ wizardConfig.domain || '-' }}</a-descriptions-item>
          <a-descriptions-item label="生成模型">{{ selectedModelLabel || '系统默认模型' }}</a-descriptions-item>
          <a-descriptions-item label="目标分类">{{ wizardConfig.categories.length ? wizardConfig.categories.join('、') : '-' }}</a-descriptions-item>
          <a-descriptions-item label="使用种子字典">{{ wizardConfig.use_seed ? '是' : '否' }}</a-descriptions-item>
          <a-descriptions-item label="重复处理策略">确定性合并 (merge)</a-descriptions-item>
        </a-descriptions>
        <a-alert
          type="warning"
          show-icon
          style="margin-top: 12px"
          message="上传文件仅用于本次生成，不会写入知识库；生成任务在后台执行，可离开页面，任务进度会自动保存并在刷新后恢复。"
        />
      </div>

      <div class="wizard-footer">
        <a-button v-if="wizardStep > 0" @click="wizardStep--">上一步</a-button>
        <a-button v-if="wizardStep < 2" type="primary" @click="wizardNext">下一步</a-button>
        <a-button v-else type="primary" :loading="wizardSubmitting" @click="submitGenerate">提交任务</a-button>
      </div>
    </a-modal>

    <!-- 任务面板 -->
    <a-drawer v-model:open="taskPanelOpen" title="任务面板" placement="right" width="480">
      <a-empty v-if="jobs.length === 0" description="暂无任务" />
      <div v-for="job in jobs" :key="job.jobId" class="job-card">
        <div class="job-card-header">
          <span class="job-title">{{ job.dictionaryName || '未知字典' }}</span>
          <a-tag :color="jobStatusColor(jobStatusOf(job))">{{ jobStatusLabel(jobStatusOf(job)) }}</a-tag>
        </div>
        <div class="job-meta">
          <span class="mono">#{{ job.jobId }}</span>
          <span v-if="jobDetailOf(job) && jobDetailOf(job).job_type" class="muted">
            {{ jobTypeLabel(jobDetailOf(job).job_type) }}
          </span>
          <span class="muted">{{ formatTs(job.ts) }}</span>
        </div>
        <template v-if="jobDetailOf(job)">
          <div class="job-stage">
            <span v-if="jobDetailOf(job).stage">阶段：{{ jobDetailOf(job).stage }}</span>
            <span v-else class="muted">阶段：-</span>
          </div>
          <a-progress
            :percent="Math.round((jobDetailOf(job).progress || 0) * 100)"
            :status="progressStatus(jobStatusOf(job))"
            size="small"
          />
          <div class="job-stats">
            <span>文件 {{ jobDetailOf(job).processed_files ?? 0 }}</span>
            <span>分块 {{ jobDetailOf(job).processed_chunks ?? 0 }}</span>
            <span>候选 {{ jobDetailOf(job).candidate_count ?? 0 }}</span>
            <span>合并 {{ jobDetailOf(job).merged_count ?? 0 }}</span>
            <span>冲突 {{ jobDetailOf(job).conflict_count ?? 0 }}</span>
            <span>待审 {{ jobDetailOf(job).pending_count ?? 0 }}</span>
            <span>拒绝 {{ jobDetailOf(job).rejected_count ?? 0 }}</span>
            <span>失败 {{ jobDetailOf(job).failed_count ?? 0 }}</span>
          </div>
          <a-alert
            v-if="jobDetailOf(job).error_summary"
            type="error"
            show-icon
            :message="jobDetailOf(job).error_summary"
            class="job-error"
          />
        </template>
        <div class="job-actions">
          <a-button
            v-if="['queued', 'running', 'cancelling'].includes(jobStatusOf(job))"
            size="small"
            @click="handleCancelJob(job)"
          >取消</a-button>
          <a-button
            v-if="['failed', 'cancelled', 'interrupted'].includes(jobStatusOf(job))"
            size="small"
            @click="handleRetryJob(job)"
          >重试</a-button>
          <a-button
            v-if="['completed', 'cancelled'].includes(jobStatusOf(job)) && jobDetailOf(job) && jobDetailOf(job).dictionary_id"
            size="small"
            type="link"
            @click="goJobDictionary(jobDetailOf(job).dictionary_id)"
          >查看字典</a-button>
        </div>
      </div>
    </a-drawer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { BookPlus, Wand2, Database, ListTodo, Upload } from 'lucide-vue-next'
import { useUserStore } from '@/stores/user'
import HeaderComponent from '@/components/HeaderComponent.vue'
import { knowledgeDictionaryApi } from '@/apis/knowledgeDictionary'
import { apiGet } from '@/apis/base'
import { chatApi } from '@/apis/auth_api'

const router = useRouter()
const userStore = useUserStore()

/** admin 与 superadmin 在知识字典功能内等价。 */
const isManager = computed(() => userStore.userRole !== 'user')

/** 统一错误提示：detail?.message || message || 操作失败 */
function errMsg(e, fallback = '操作失败') {
  const detail = e && e.detail
  if (detail) {
    if (typeof detail === 'string' && detail) return detail
    if (typeof detail === 'object' && detail.message) return detail.message
  }
  const m = e && e.message
  if (typeof m === 'string' && m && m !== '[object Object]') return m
  return fallback
}

function formatTime(t) {
  if (!t) return '-'
  return String(t).replace('T', ' ').slice(0, 19)
}

function formatTs(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// ---------------------------------------------------------------------------
// 字典列表
// ---------------------------------------------------------------------------

const state = reactive({ loading: false })
const filters = reactive({ keyword: '', status: undefined, domain: '' })
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const statusOptions = [
  { label: '草稿', value: 'draft' },
  { label: '已发布', value: 'published' },
  { label: '已撤回', value: 'withdrawn' },
]

const statusLabel = (s) => ({ draft: '草稿', published: '已发布', withdrawn: '已撤回' }[s] || s || '-')
const statusColor = (s) => ({ draft: 'default', published: 'green', withdrawn: 'orange' }[s] || 'default')
const indexLabel = (s) => ({ pending: '待索引', embedding: '嵌入中', indexed: '已索引', verified: '已验证', ready: '就绪', failed: '失败' }[s] || s || '-')
const indexColor = (s) => ({ pending: 'default', embedding: 'processing', indexed: 'blue', verified: 'cyan', ready: 'green', failed: 'red' }[s] || 'default')
const sourceTypeLabel = (s) => ({ knowledge_base_file: '知识库文件', knowledge_base: '知识库', upload: '上传文件' }[s] || s)
const sourceTypeColor = (s) => ({ knowledge_base_file: 'blue', knowledge_base: 'purple', upload: 'cyan' }[s] || 'default')

const columns = [
  { title: '名称', key: 'name', dataIndex: 'name', width: 220 },
  { title: '专业领域', key: 'domain', dataIndex: 'domain', width: 130 },
  { title: '活动版本', key: 'active_version', width: 160 },
  { title: '条目数', key: 'entry_count', width: 90 },
  { title: '来源类型', key: 'source_types', width: 180 },
  { title: '发布状态', key: 'status', width: 100 },
  { title: '索引状态', key: 'index_status', width: 100 },
  { title: '更新时间', key: 'updated_at', width: 160 },
  { title: '操作', key: 'action', width: 170 },
]

async function loadDictionaries() {
  state.loading = true
  try {
    const res = await knowledgeDictionaryApi.listDictionaries({
      keyword: filters.keyword.trim(),
      status: filters.status || '',
      domain: filters.domain.trim(),
      page: page.value,
      pageSize: pageSize.value,
    })
    const data = res && res.data ? res.data : {}
    items.value = data.items || []
    total.value = data.total || 0
    // 当前页被删空时回退到最后一页
    if (items.value.length === 0 && total.value > 0 && page.value > 1) {
      page.value = Math.max(1, Math.ceil(total.value / pageSize.value))
      await loadDictionaries()
    }
  } catch (e) {
    message.error(errMsg(e, '加载字典列表失败'))
  } finally {
    state.loading = false
  }
}

function handleSearch() {
  page.value = 1
  loadDictionaries()
}

function resetFilters() {
  filters.keyword = ''
  filters.status = undefined
  filters.domain = ''
  handleSearch()
}

function onPageChange(p) {
  page.value = p
  loadDictionaries()
}

function onSizeChange(_current, size) {
  pageSize.value = size
  page.value = 1
  loadDictionaries()
}

function goDetail(record) {
  router.push(`/knowledge-dictionaries/${record.id}`)
}

// ---------------------------------------------------------------------------
// 新建 / 编辑 / 删除
// ---------------------------------------------------------------------------

const createModal = reactive({ open: false, loading: false })
const createForm = reactive({ name: '', description: '', domain: '' })

function openCreateModal() {
  Object.assign(createForm, { name: '', description: '', domain: '' })
  createModal.open = true
}

async function submitCreate() {
  if (!createForm.name.trim()) {
    message.warning('请填写字典名称')
    return
  }
  createModal.loading = true
  try {
    await knowledgeDictionaryApi.createDictionary({
      name: createForm.name.trim(),
      description: createForm.description.trim() || null,
      domain: createForm.domain.trim() || null,
    })
    message.success('字典已创建')
    createModal.open = false
    await loadDictionaries()
  } catch (e) {
    message.error(errMsg(e, '创建失败'))
  } finally {
    createModal.loading = false
  }
}

const editModal = reactive({ open: false, loading: false })
const editForm = reactive({ id: null, name: '', description: '', domain: '' })

function openEditModal(record) {
  Object.assign(editForm, {
    id: record.id,
    name: record.name || '',
    description: record.description || '',
    domain: record.domain || '',
  })
  editModal.open = true
}

async function submitEdit() {
  if (!editForm.name.trim()) {
    message.warning('请填写字典名称')
    return
  }
  editModal.loading = true
  try {
    await knowledgeDictionaryApi.updateDictionary(editForm.id, {
      name: editForm.name.trim(),
      description: editForm.description.trim() || null,
      domain: editForm.domain.trim() || null,
    })
    message.success('字典已更新')
    editModal.open = false
    await loadDictionaries()
  } catch (e) {
    message.error(errMsg(e, '更新失败'))
  } finally {
    editModal.loading = false
  }
}

function handleDelete(record) {
  Modal.confirm({
    title: '删除字典',
    content:
      record.status === 'published'
        ? `「${record.name}」当前为已发布状态，请先撤回该字典后再删除。确定仍要删除吗？`
        : `确定删除字典「${record.name}」？删除后不可恢复。`,
    okType: 'danger',
    async onOk() {
      try {
        await knowledgeDictionaryApi.deleteDictionary(record.id)
        message.success('字典已删除')
        await loadDictionaries()
      } catch (e) {
        message.error(errMsg(e, '删除失败'))
      }
    },
  })
}

// ---------------------------------------------------------------------------
// 种子迁移
// ---------------------------------------------------------------------------

const seedLoading = ref(false)

function handleSeedImport() {
  Modal.confirm({
    title: '种子迁移',
    content: '将 XinJiang 旧版压裂字典资产迁移为种子字典（幂等，重复执行不会创建重复数据）。确定开始吗？',
    async onOk() {
      seedLoading.value = true
      try {
        const res = await knowledgeDictionaryApi.seedImport()
        const job = res && res.data ? res.data : {}
        if (job.id) {
          addJobToStorage({
            jobId: job.id,
            dictionaryName: '种子迁移',
            ts: Date.now(),
            status: job.status || 'queued',
          })
          taskPanelOpen.value = true
          startPolling()
          message.success('种子迁移任务已创建')
          loadDictionaries()
        }
      } catch (e) {
        message.error(errMsg(e, '种子迁移失败'))
      } finally {
        seedLoading.value = false
      }
    },
  })
}

// ---------------------------------------------------------------------------
// 生成向导
// ---------------------------------------------------------------------------

const wizardVisible = ref(false)
const wizardStep = ref(0)
const sourceKind = ref('kb_file')
const kbLoading = ref(false)
const kbList = ref([])
const sourceKbId = ref('')
const kbFilesLoading = ref(false)
const kbFiles = ref([])
const kbFilesTotal = ref(0)
const kbFilesPage = ref(1)
const kbFilesPageSize = ref(10)
const kbFilesKeyword = ref('')
const selectedFile = ref(null)
const uploadLoading = ref(false)
const uploadFile = ref(null)
const wizardDictList = ref([])
const modelOptions = ref([])
const wizardSubmitting = ref(false)

const wizardConfig = reactive({
  dictionary_id: '',
  name: '',
  domain: '',
  description: '',
  model_id: '',
  categories: [],
  use_seed: true,
  duplicate_policy: 'merge',
})

const kbOptions = computed(() =>
  kbList.value.map((kb) => ({ label: `${kb.name}（文件 ${kb.file_count}）`, value: kb.db_id })),
)
const sourceKb = computed(() => kbList.value.find((kb) => kb.db_id === sourceKbId.value) || null)
const fileSelectedKeys = computed(() => (selectedFile.value ? [selectedFile.value.file_id] : []))
const wizardDictOptions = computed(() => [
  { label: '新建字典', value: '' },
  ...wizardDictList.value.map((d) => ({ label: d.name, value: String(d.id) })),
])
const sourceKindLabel = (k) => ({ kb_file: '知识库文件', upload: '上传文件', kb: '整个知识库' }[k] || k)

const sourceSummaryText = computed(() => {
  if (sourceKind.value === 'kb_file') return selectedFile.value ? selectedFile.value.file_name : '-'
  if (sourceKind.value === 'upload') return uploadFile.value ? uploadFile.value.file_name : '-'
  return sourceKb.value ? sourceKb.value.name : '-'
})
const expectedFileCount = computed(() => {
  if (sourceKind.value === 'kb_file' || sourceKind.value === 'upload') return 1
  return sourceKb.value ? sourceKb.value.file_count || 0 : 0
})
const finalDictName = computed(() => {
  if (wizardConfig.dictionary_id) {
    const d = wizardDictList.value.find((x) => String(x.id) === String(wizardConfig.dictionary_id))
    return d ? d.name : '-'
  }
  return wizardConfig.name.trim() || '-'
})
const selectedModelLabel = computed(() => {
  const opt = modelOptions.value.find((o) => o.value === String(wizardConfig.model_id))
  return opt && opt.value !== '' ? opt.label : ''
})

const fileColumns = [
  { title: '文件名', key: 'file_name', dataIndex: 'file_name' },
  { title: '类型', dataIndex: 'file_type', width: 100 },
  { title: '状态', dataIndex: 'status', width: 90 },
  { title: '节点数', key: 'node_count', dataIndex: 'node_count', width: 90 },
]

function openWizard() {
  wizardVisible.value = true
  wizardStep.value = 0
  sourceKind.value = 'kb_file'
  sourceKbId.value = ''
  kbFiles.value = []
  kbFilesTotal.value = 0
  kbFilesPage.value = 1
  kbFilesPageSize.value = 10
  kbFilesKeyword.value = ''
  selectedFile.value = null
  uploadFile.value = null
  Object.assign(wizardConfig, {
    dictionary_id: '',
    name: '',
    domain: '',
    description: '',
    model_id: '',
    categories: [],
    use_seed: true,
    duplicate_policy: 'merge',
  })
  loadKbList()
  loadModels()
  loadWizardDicts()
}

async function loadKbList() {
  kbLoading.value = true
  try {
    const res = await apiGet('/api/knowledge-dictionaries/sources/knowledge-bases', {}, true)
    kbList.value = res && res.data ? res.data : []
  } catch (e) {
    message.error(errMsg(e, '加载知识库列表失败'))
  } finally {
    kbLoading.value = false
  }
}

async function loadModels() {
  try {
    const models = await chatApi.getUserModels()
    const list = Array.isArray(models) ? models : []
    modelOptions.value = [
      { label: '系统默认模型', value: '' },
      ...list.map((m) => ({ label: `${m.display_name} (${m.model_name})`, value: String(m.id) })),
    ]
  } catch (e) {
    message.error(errMsg(e, '加载生成模型失败'))
  }
}

async function loadWizardDicts() {
  try {
    const res = await knowledgeDictionaryApi.listDictionaries({ page: 1, pageSize: 100 })
    wizardDictList.value = res && res.data ? res.data.items || [] : []
  } catch (e) {
    console.error('加载字典列表失败:', e)
  }
}

function onWizardDictChange(val) {
  if (val) {
    const d = wizardDictList.value.find((x) => String(x.id) === String(val))
    if (d) wizardConfig.name = d.name
  }
}

function onSourceKbChange(val) {
  selectedFile.value = null
  if (val) {
    kbFilesPage.value = 1
    kbFilesKeyword.value = ''
    loadKbFiles()
  } else {
    kbFiles.value = []
    kbFilesTotal.value = 0
  }
}

async function loadKbFiles() {
  if (!sourceKbId.value) return
  kbFilesLoading.value = true
  try {
    const url =
      `/api/knowledge-dictionaries/sources/knowledge-bases/${encodeURIComponent(sourceKbId.value)}/files` +
      `?keyword=${encodeURIComponent(kbFilesKeyword.value.trim())}&page=${kbFilesPage.value}&page_size=${kbFilesPageSize.value}`
    const res = await apiGet(url, {}, true)
    const data = res && res.data ? res.data : {}
    kbFiles.value = data.items || []
    kbFilesTotal.value = data.total || 0
  } catch (e) {
    message.error(errMsg(e, '加载知识库文件失败'))
  } finally {
    kbFilesLoading.value = false
  }
}

function onKbFileSearch() {
  kbFilesPage.value = 1
  loadKbFiles()
}

function onKbFileSizeChange(_current, size) {
  kbFilesPageSize.value = size
  kbFilesPage.value = 1
  loadKbFiles()
}

function onFileSelect(_keys, rows) {
  selectedFile.value = rows[0] || null
}

function fileCheckboxProps(record) {
  return { disabled: record.node_count === 0 }
}

const allowedExts = ['pdf', 'docx', 'xlsx', 'csv', 'txt']
const MAX_UPLOAD_BYTES = 100 * 1024 * 1024

function handleBeforeUpload(file) {
  const ext = (file.name.split('.').pop() || '').toLowerCase()
  if (!allowedExts.includes(ext)) {
    message.error('仅支持 .pdf / .docx / .xlsx / .csv / .txt 文件')
    return false
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    message.error('文件大小不能超过 100MB')
    return false
  }
  doUpload(file)
  return false
}

async function doUpload(file) {
  uploadLoading.value = true
  try {
    const res = await knowledgeDictionaryApi.uploadSource(file)
    uploadFile.value = res && res.data ? res.data : {}
    message.success('文件上传成功')
  } catch (e) {
    message.error(errMsg(e, '上传失败'))
  } finally {
    uploadLoading.value = false
  }
}

function clearUpload() {
  uploadFile.value = null
}

function wizardNext() {
  if (wizardStep.value === 0) {
    if (sourceKind.value === 'kb_file' && !selectedFile.value) {
      message.warning('请选择一个知识库文件')
      return
    }
    if (sourceKind.value === 'upload' && !uploadFile.value) {
      message.warning('请先上传来源文件')
      return
    }
    if (sourceKind.value === 'kb' && !sourceKbId.value) {
      message.warning('请选择知识库')
      return
    }
  }
  if (wizardStep.value === 1) {
    if (!wizardConfig.dictionary_id && !wizardConfig.name.trim()) {
      message.warning('新建字典时必须填写字典名称')
      return
    }
  }
  wizardStep.value++
}

async function submitGenerate() {
  if (wizardStep.value < 2) return
  wizardSubmitting.value = true
  try {
    const source = {}
    if (sourceKind.value === 'kb_file') {
      source.kind = 'kb_file'
      source.db_id = sourceKbId.value
      source.file_id = selectedFile.value.file_id
    } else if (sourceKind.value === 'upload') {
      source.kind = 'upload'
      source.storage_ref = uploadFile.value.storage_ref
      source.file_name = uploadFile.value.file_name || null
    } else {
      source.kind = 'kb'
      source.db_id = sourceKbId.value
    }
    const res = await knowledgeDictionaryApi.generate({
      name: wizardConfig.dictionary_id ? null : wizardConfig.name.trim(),
      description: wizardConfig.description.trim() || null,
      domain: wizardConfig.domain.trim() || null,
      dictionary_id: wizardConfig.dictionary_id ? Number(wizardConfig.dictionary_id) : null,
      model_id: wizardConfig.model_id === '' ? null : Number(wizardConfig.model_id),
      categories: wizardConfig.categories.length ? wizardConfig.categories : null,
      use_seed: wizardConfig.use_seed,
      duplicate_policy: 'merge',
      source,
    })
    const job = res && res.data ? res.data : {}
    if (job.id) {
      addJobToStorage({
        jobId: job.id,
        dictionaryName: finalDictName.value,
        ts: Date.now(),
        status: job.status || 'queued',
      })
    }
    message.success('生成任务已提交')
    wizardVisible.value = false
    taskPanelOpen.value = true
    startPolling()
    loadDictionaries()
  } catch (e) {
    message.error(errMsg(e, '提交生成任务失败'))
  } finally {
    wizardSubmitting.value = false
  }
}

// ---------------------------------------------------------------------------
// 任务面板（localStorage kd_last_jobs 持久化 + getJob 轮询）
// ---------------------------------------------------------------------------

const JOB_KEY = 'kd_last_jobs'
const ACTIVE_JOB_STATUSES = ['queued', 'running', 'cancelling']
const TERMINAL_JOB_STATUSES = ['cancelled', 'completed', 'failed', 'interrupted']

const taskPanelOpen = ref(false)
const jobs = ref(readStoredJobs())
const jobDetails = ref({})

function readStoredJobs() {
  try {
    const arr = JSON.parse(localStorage.getItem(JOB_KEY) || '[]')
    return Array.isArray(arr) ? arr : []
  } catch (e) {
    console.error('读取任务列表失败:', e)
    return []
  }
}

function persistJobs() {
  try {
    localStorage.setItem(JOB_KEY, JSON.stringify(jobs.value))
  } catch (e) {
    console.error('保存任务列表失败:', e)
  }
}

function addJobToStorage(job) {
  jobs.value = [job, ...jobs.value.filter((j) => j.jobId !== job.jobId)]
  persistJobs()
}

function updateJobStatus(jobId, status) {
  const j = jobs.value.find((x) => x.jobId === jobId)
  if (j && j.status !== status) {
    j.status = status
    persistJobs()
  }
}

function jobDetailOf(job) {
  return jobDetails.value[job.jobId] || null
}

function jobStatusOf(job) {
  const detail = jobDetailOf(job)
  return detail && detail.status ? detail.status : job.status
}

const activeJobCount = computed(
  () => jobs.value.filter((j) => ACTIVE_JOB_STATUSES.includes(j.status)).length,
)
const activeJobs = computed(() =>
  jobs.value.filter((j) => ACTIVE_JOB_STATUSES.includes(j.status)),
)

const jobStatusLabel = (s) => ({ queued: '排队中', running: '运行中', cancelling: '取消中', cancelled: '已取消', completed: '已完成', failed: '失败', interrupted: '中断' }[s] || s || '-')
const jobStatusColor = (s) => ({ queued: 'default', running: 'processing', cancelling: 'orange', cancelled: 'default', completed: 'green', failed: 'red', interrupted: 'orange' }[s] || 'default')
const jobTypeLabel = (t) => ({ generate: '生成', index: '索引', import_seed: '种子迁移', export: '导出' }[t] || t || '')

function progressStatus(status) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'exception'
  if (status === 'cancelled' || status === 'interrupted') return 'normal'
  return 'active'
}

function openTaskPanel() {
  taskPanelOpen.value = true
  startPolling()
}

let pollTimer = null

function startPolling() {
  stopPolling()
  if (activeJobs.value.length === 0) return
  pollTimer = setInterval(pollActiveJobs, 2000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function pollActiveJobs() {
  const actives = activeJobs.value
  if (!actives.length) {
    stopPolling()
    return
  }
  for (const job of actives) {
    try {
      const res = await knowledgeDictionaryApi.getJob(job.jobId)
      const detail = res && res.data ? res.data : res
      if (detail && detail.id) {
        jobDetails.value = { ...jobDetails.value, [job.jobId]: detail }
        if (detail.status && TERMINAL_JOB_STATUSES.includes(detail.status)) {
          updateJobStatus(job.jobId, detail.status)
        }
      }
    } catch (e) {
      console.error(`任务 ${job.jobId} 轮询失败:`, e)
    }
  }
}

async function handleCancelJob(job) {
  try {
    const res = await knowledgeDictionaryApi.cancelJob(job.jobId)
    const detail = res && res.data ? res.data : res
    if (detail && detail.id) {
      jobDetails.value = { ...jobDetails.value, [job.jobId]: detail }
      if (detail.status) updateJobStatus(job.jobId, detail.status)
    }
    message.success('取消请求已受理')
  } catch (e) {
    message.error(errMsg(e, '取消失败'))
  }
}

async function handleRetryJob(job) {
  try {
    const res = await knowledgeDictionaryApi.retryJob(job.jobId)
    const detail = res && res.data ? res.data : res
    if (detail && detail.id) {
      jobDetails.value = { ...jobDetails.value, [job.jobId]: detail }
      if (detail.status) updateJobStatus(job.jobId, detail.status)
    }
    message.success('任务已重新排队')
    startPolling()
  } catch (e) {
    message.error(errMsg(e, '重试失败'))
  }
}

function goJobDictionary(dictionaryId) {
  router.push(`/knowledge-dictionaries/${dictionaryId}`)
}

onMounted(() => {
  loadDictionaries()
  startPolling()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<style lang="less" scoped>
.kd-view {
  min-height: 100%;
}

// 顶部操作按钮：图标与文字垂直居中对齐（lucide SVG 默认行内基线会错位）
.kd-view :deep(.ant-btn) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.kd-view :deep(.ant-btn .anticon) {
  display: inline-flex;
  align-items: center;
  line-height: 1;
}

.kd-view :deep(.ant-btn .anticon svg) {
  display: block;
  vertical-align: middle;
}

.kd-body {
  padding: 16px 24px;
}

.kd-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.kd-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.kd-name {
  padding: 0;
  font-weight: 500;
}

.v-no {
  font-weight: 600;
  margin-right: 6px;
  color: var(--text-primary);
}

.muted {
  color: var(--gray-600);
}

.mono {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}

.wizard-step {
  padding-top: 4px;
}

.wizard-source-kind {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.wizard-source-body {
  margin-top: 12px;
}

.kb-file-picker {
  margin-top: 12px;
}

.kb-file-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.kb-stats {
  display: flex;
  gap: 40px;
  margin-top: 16px;
  margin-bottom: 12px;
}

.upload-file-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.wizard-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}

.job-card {
  border: 1px solid var(--gray-300);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  background: var(--surface-raised);
}

.job-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.job-title {
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
  font-size: 12px;
}

.job-stage {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-primary);
}

.job-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--gray-700);
}

.job-error {
  margin-top: 8px;
}

.job-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
</style>
