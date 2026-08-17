<template>
  <div class="kd-detail">
    <template v-if="show404">
      <a-result status="404" title="字典不存在" sub-title="该字典不存在或已被删除">
        <template #extra>
          <a-button type="primary" @click="goBack">返回</a-button>
        </template>
      </a-result>
    </template>

    <template v-else-if="loadFailed">
      <a-result status="warning" :title="loadFailedMsg" sub-title="请稍后重试">
        <template #extra>
          <a-button type="primary" @click="goBack">返回</a-button>
        </template>
      </a-result>
    </template>

    <template v-else>
      <HeaderComponent :title="dict ? dict.name : '知识字典详情'" :loading="loading">
        <template #actions>
          <a-button @click="goBack">
            <template #icon><ArrowLeft :size="16" /></template>
            返回
          </a-button>
        </template>
      </HeaderComponent>

      <div v-if="dict" class="kd-detail-body">
        <a-descriptions v-if="dict.description || dict.domain" :column="2" size="small" class="dict-desc">
          <a-descriptions-item v-if="dict.domain" label="专业领域">{{ dict.domain }}</a-descriptions-item>
          <a-descriptions-item v-if="dict.description" label="说明">{{ dict.description }}</a-descriptions-item>
        </a-descriptions>

        <!-- 版本选择与操作 -->
        <div class="section-block">
          <div class="section-title">版本</div>
          <a-radio-group v-model:value="currentVersionId" class="version-list" @change="onVersionChange">
            <a-radio v-for="v in visibleVersions" :key="v.id" :value="v.id" class="version-item">
              <div class="version-info">
                <div class="version-line">
                  <span class="v-no">V{{ v.version_no }}</span>
                  <a-tag :color="versionStatusColor(v.status)">{{ versionStatusLabel(v.status) }}</a-tag>
                  <a-tag v-if="v.id === dict.active_version_id" color="gold">活动版本</a-tag>
                  <a-tag v-if="v.index_status" :color="indexColor(v.index_status)">{{ indexLabel(v.index_status) }}</a-tag>
                </div>
                <div class="version-line muted">
                  条目 {{ v.entry_count ?? 0 }} · 待审 {{ v.pending_count ?? 0 }} · 冲突 {{ v.conflict_count ?? 0 }} · 向量 {{ v.vector_count ?? 0 }}
                </div>
                <div class="version-line muted">创建于 {{ formatTime(v.created_at) }}</div>
              </div>
            </a-radio>
          </a-radio-group>

          <div v-if="isManager && currentVersion" class="version-actions">
            <a-button v-if="isActivePublished" danger @click="handleWithdraw">
              <template #icon><Undo2 :size="16" /></template>
              撤回
            </a-button>
            <a-button v-if="versionEditable" type="primary" @click="handlePublish">
              <template #icon><Play :size="16" /></template>
              发布
            </a-button>
            <a-button v-if="versionEditable" @click="handleBuildIndex">
              <template #icon><RefreshCw :size="16" /></template>
              重建索引
            </a-button>
            <a-dropdown>
              <a-button>
                导出
                <DownOutlined />
              </a-button>
              <template #overlay>
                <a-menu @click="onExportMenu">
                  <a-menu-item key="xlsx">导出 XLSX</a-menu-item>
                  <a-menu-item key="csv">导出 CSV</a-menu-item>
                  <a-menu-item key="json">导出 JSON</a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </div>
        </div>

        <!-- 只读检索（已发布字典） -->
        <div v-if="dict.status === 'published'" class="section-block">
          <div class="section-title">字典检索</div>
          <a-input-search
            v-model:value="searchQuery"
            placeholder="输入关键词检索字典条目（语义检索，Top 10）"
            enter-button="检索"
            :loading="searchLoading"
            @search="doSearch"
          />
          <a-empty v-if="searched && !searchLoading && searchResults.length === 0" description="未检索到相关条目" />
          <div v-for="r in searchResults" :key="r.id" class="search-result">
            <div class="search-result-head">
              <a-tag color="blue">V{{ r.version_no }}</a-tag>
              <span class="search-name">{{ r.standard_name }}</span>
              <span v-if="typeof r.similarity === 'number'" class="search-sim">
                <a-tag :color="similarityColor(r.similarity)">相似度 {{ similarityPercent(r.similarity) }}%</a-tag>
              </span>
            </div>
            <div v-if="r.definition" class="search-def">{{ r.definition }}</div>
            <div class="search-meta muted">
              <span v-if="r.category">分类：{{ r.category }}</span>
              <span v-if="r.unit">单位：{{ r.unit }}</span>
              <span v-if="r.data_type">类型：{{ r.data_type }}</span>
              <span v-if="r.synonyms && r.synonyms.length">同义词：{{ r.synonyms.join('、') }}</span>
              <span v-if="r.value_rule">规则：{{ r.value_rule }}</span>
              <span v-if="r.dictionary_name">字典：{{ r.dictionary_name }}</span>
            </div>
            <div v-if="r.evidence_summary && r.evidence_summary.length" class="search-evidence">
              <div v-for="(ev, i) in r.evidence_summary" :key="i" class="search-evidence-item">
                <span class="quote">“{{ ev.quote }}”</span>
                <span class="muted">
                  {{ ev.file_name || '未知来源' }}
                  <template v-if="ev.page_no"> · 第 {{ ev.page_no }} 页</template>
                  <template v-if="ev.sheet_name"> · {{ ev.sheet_name }}</template>
                  <template v-if="ev.cell_range"> · {{ ev.cell_range }}</template>
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- 条目（工作台 / 只读） -->
        <div class="section-block">
          <div class="section-title">条目{{ versionEditable ? '工作台' : '' }}</div>
          <div class="entries-toolbar">
            <template v-if="versionEditable">
              <a-input v-model:value="entryFilters.category" placeholder="分类" allow-clear style="width: 130px" @press-enter="handleEntrySearch" />
              <a-select v-model:value="entryFilters.reviewStatus" placeholder="审核状态" allow-clear style="width: 130px" :options="reviewStatusOptions" @change="handleEntrySearch" />
              <a-input v-model:value="entryFilters.keyword" placeholder="关键字" allow-clear style="width: 150px" @press-enter="handleEntrySearch" />
              <a-input v-model:value="entryFilters.sourceFile" placeholder="来源文件" allow-clear style="width: 150px" @press-enter="handleEntrySearch" />
              <a-input-number v-model:value="entryFilters.minConfidence" :min="0" :max="1" :step="0.05" placeholder="最低置信度" style="width: 130px" />
              <a-tooltip title="仅显示缺失必填字段的条目">
                <a-switch v-model:checked="entryFilters.missingFields" checked-children="缺失字段" un-checked-children="缺失字段" />
              </a-tooltip>
              <a-tooltip title="仅显示冲突条目">
                <a-switch v-model:checked="entryFilters.conflictOnly" checked-children="仅冲突" un-checked-children="仅冲突" />
              </a-tooltip>
              <a-button type="primary" @click="handleEntrySearch">查询</a-button>
              <a-button @click="resetEntryFilters">重置</a-button>
            </template>
            <template v-else>
              <a-input v-model:value="roKeyword" placeholder="按名称/定义搜索" allow-clear style="width: 220px" @press-enter="handleEntrySearch" />
              <a-button type="primary" @click="handleEntrySearch">查询</a-button>
              <a-button @click="resetEntryFilters">重置</a-button>
            </template>
            <template v-if="versionEditable">
              <a-divider type="vertical" />
              <a-button type="primary" @click="openEntryCreate">
                <template #icon><Plus :size="16" /></template>
                新建条目
              </a-button>
              <a-button @click="handleBatchApprove">批量通过</a-button>
              <a-button @click="handleBatchReject">批量驳回</a-button>
              <a-button @click="handleMergeSelected">合并所选</a-button>
              <a-button @click="openMergeSuggestions">
                <template #icon><Lightbulb :size="16" /></template>
                合并建议
              </a-button>
            </template>
          </div>

          <a-table
            :columns="entryColumns"
            :data-source="entries"
            row-key="id"
            :loading="entriesLoading"
            :pagination="false"
            size="middle"
            bordered
            :row-selection="rowSelection"
            :scroll="{ x: 1200 }"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'standard_name'">
                <span class="entry-name">{{ record.standard_name }}</span>
              </template>
              <template v-else-if="column.key === 'definition'">
                <a-tooltip :title="record.definition">
                  <span class="ellipsis-text">{{ record.definition }}</span>
                </a-tooltip>
              </template>
              <template v-else-if="column.key === 'unit'">
                <span v-if="record.unit">{{ record.unit }}</span>
                <span v-else class="muted">-</span>
              </template>
              <template v-else-if="column.key === 'data_type'">
                <span v-if="record.data_type">{{ record.data_type }}</span>
                <span v-else class="muted">-</span>
              </template>
              <template v-else-if="column.key === 'synonyms'">
                <template v-if="record.synonyms && record.synonyms.length">
                  <a-tag v-for="s in record.synonyms.slice(0, 3)" :key="s">{{ s }}</a-tag>
                  <a-tag v-if="record.synonyms.length > 3">+{{ record.synonyms.length - 3 }}</a-tag>
                </template>
                <span v-else class="muted">-</span>
              </template>
              <template v-else-if="column.key === 'value_rule'">
                <a-tooltip :title="record.value_rule || ''">
                  <span class="ellipsis-text">{{ record.value_rule || '-' }}</span>
                </a-tooltip>
              </template>
              <template v-else-if="column.key === 'confidence'">
                <a-tooltip :title="confidenceTitle(record.confidence)">
                  <a-tag :color="confidenceColor(record.confidence)">{{ confidenceText(record.confidence) }}</a-tag>
                </a-tooltip>
              </template>
              <template v-else-if="column.key === 'review_status'">
                <a-tag :color="reviewStatusColor(record.review_status)">{{ reviewStatusLabel(record.review_status) }}</a-tag>
              </template>
              <template v-else-if="column.key === 'action'">
                <a-button type="link" size="small" @click="openEvidence(record)">证据</a-button>
                <a-button type="link" size="small" @click="openEntryEdit(record)">编辑</a-button>
                <a-button type="link" size="small" :disabled="record.review_status === 'approved'" @click="reviewEntryAction(record, 'approve')">通过</a-button>
                <a-button type="link" size="small" :disabled="record.review_status === 'rejected'" @click="reviewEntryAction(record, 'reject')">驳回</a-button>
                <a-button type="link" size="small" :disabled="record.review_status === 'pending'" @click="reviewEntryAction(record, 'reset')">重置</a-button>
                <a-button type="link" size="small" danger @click="handleDeleteEntry(record)">删除</a-button>
              </template>
            </template>
          </a-table>
          <div class="entries-pagination">
            <a-pagination
              v-model:current="entryPage"
              v-model:page-size="entryPageSize"
              :total="entryTotal"
              show-size-changer
              show-total
              @change="onEntryPageChange"
              @show-size-change="onEntrySizeChange"
            />
          </div>
        </div>
      </div>
    </template>

    <!-- 证据抽屉 -->
    <a-drawer v-model:open="evidenceDrawer.open" :title="evidenceDrawer.title" placement="right" width="480">
      <a-empty v-if="!evidenceDrawer.loading && evidenceDrawer.items.length === 0" description="暂无证据" />
      <div v-for="ev in evidenceDrawer.items" :key="ev.id" class="evidence-card">
        <pre class="evidence-quote">{{ ev.quote }}</pre>
        <div class="evidence-meta">
          <a-tag v-if="ev.field_path" color="blue">{{ ev.field_path }}</a-tag>
          <a-badge v-if="ev.inferred" count="推断" :number-style="{ backgroundColor: '#fa8c16' }" />
          <span class="muted">{{ ev.source_file_name || '未知来源文件' }}</span>
        </div>
        <div class="evidence-loc muted">
          <span v-if="ev.page_no">第 {{ ev.page_no }} 页</span>
          <span v-if="ev.sheet_name">{{ ev.sheet_name }}</span>
          <span v-if="ev.cell_range">{{ ev.cell_range }}</span>
        </div>
      </div>
    </a-drawer>

    <!-- 条目 新建/编辑 -->
    <a-drawer v-model:open="entryEditor.open" :title="entryEditor.title" width="640">
      <a-form :model="entryEditor.form" layout="vertical">
        <a-form-item label="分类">
          <a-input v-model:value="entryEditor.form.category" maxlength="255" placeholder="如：压裂" />
        </a-form-item>
        <a-form-item label="标准名称" required>
          <a-input v-model:value="entryEditor.form.standard_name" maxlength="255" placeholder="必填" />
        </a-form-item>
        <a-form-item label="定义" required>
          <a-textarea v-model:value="entryEditor.form.definition" :rows="3" maxlength="20000" placeholder="必填" />
        </a-form-item>
        <a-form-item label="单位">
          <a-input v-model:value="entryEditor.form.unit" maxlength="100" />
        </a-form-item>
        <a-form-item label="数据类型">
          <a-select v-model:value="entryEditor.form.data_type" :options="dataTypeOptions" allow-clear placeholder="选择数据类型" />
        </a-form-item>
        <a-form-item label="同义词">
          <a-select v-model:value="entryEditor.form.synonyms" mode="tags" placeholder="输入后回车添加" />
        </a-form-item>
        <a-form-item label="取值规则">
          <a-textarea v-model:value="entryEditor.form.value_rule" :rows="2" maxlength="4000" />
        </a-form-item>
        <a-form-item label="证据" required>
          <div v-for="(ev, i) in entryEditor.form.evidences" :key="i" class="evidence-edit-row">
            <a-input v-model:value="ev.quote" placeholder="原文引用（必填）" class="ev-quote" />
            <a-input v-model:value="ev.page_no" placeholder="页码" class="ev-loc" />
            <a-input v-model:value="ev.sheet_name" placeholder="工作表" class="ev-loc" />
            <a-input v-model:value="ev.cell_range" placeholder="单元格范围" class="ev-loc" />
            <a-button type="text" danger @click="removeEvidenceRow(i)">
              <template #icon><Trash2 :size="16" /></template>
            </a-button>
          </div>
          <a-button type="dashed" block @click="addEvidenceRow">
            <template #icon><Plus :size="16" /></template>
            添加证据
          </a-button>
        </a-form-item>
      </a-form>
      <template #footer>
        <a-button style="margin-right: 8px" @click="entryEditor.open = false">取消</a-button>
        <a-button type="primary" :loading="entryEditor.saving" @click="saveEntry">保存</a-button>
      </template>
    </a-drawer>

    <!-- 合并建议 -->
    <a-modal v-model:open="suggestionsModal.open" title="合并建议" :footer="null" width="640">
      <a-empty v-if="!suggestionsModal.loading && suggestionsModal.items.length === 0" description="暂无相似条目建议" />
      <div v-for="(s, i) in suggestionsModal.items" :key="i" class="suggestion-row">
        <div class="suggestion-names">
          <span class="suggestion-name">{{ s.entry_a.standard_name }}</span>
          <a-tag color="orange">{{ (s.similarity * 100).toFixed(1) }}%</a-tag>
          <span class="suggestion-name">{{ s.entry_b.standard_name }}</span>
        </div>
        <a-button size="small" type="primary" :loading="suggestionsModal.merging === i" @click="doSuggestionMerge(s, i)">合并</a-button>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, h, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { ArrowLeft, Play, Undo2, RefreshCw, Plus, Lightbulb, Trash2 } from 'lucide-vue-next'
import { DownOutlined } from '@ant-design/icons-vue'
import { useUserStore } from '@/stores/user'
import HeaderComponent from '@/components/HeaderComponent.vue'
import { knowledgeDictionaryApi, downloadDictionaryExport } from '@/apis/knowledgeDictionary'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const dictId = computed(() => Number(route.params.dictionary_id))

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

function isNotFoundError(e) {
  const d = e && e.detail
  if (d && typeof d === 'object' && d.error_code === 'DICTIONARY_NOT_FOUND') return true
  const m = (e && e.message) || ''
  return m.includes('404') || m.includes('不存在') || m.includes('Not Found') || m === '[object Object]'
}

function formatTime(t) {
  if (!t) return '-'
  return String(t).replace('T', ' ').slice(0, 19)
}

function goBack() {
  router.back()
}

// ---------------------------------------------------------------------------
// 字典详情与版本
// ---------------------------------------------------------------------------

const loading = ref(false)
const loadFailed = ref(false)
const loadFailedMsg = ref('加载失败')
const show404 = ref(false)
const dict = ref(null)
const versions = ref([])
const currentVersionId = ref(null)

const currentVersion = computed(() => versions.value.find((v) => v.id === currentVersionId.value) || null)
/** 普通用户只展示可读版本（后端仅对可读版本返回完整字段）。 */
const visibleVersions = computed(() => {
  if (isManager.value) return versions.value
  return versions.value.filter((v) => typeof v.entry_count === 'number' || v.id === (dict.value && dict.value.active_version_id))
})
const versionEditable = computed(
  () => isManager.value && currentVersion.value && ['draft', 'reviewing'].includes(currentVersion.value.status),
)
const isActivePublished = computed(
  () => currentVersion.value && currentVersion.value.status === 'published' && currentVersion.value.id === dict.value.active_version_id,
)

const versionStatusLabel = (s) => ({ draft: '草稿', reviewing: '审核中', published: '已发布', withdrawn: '已撤回' }[s] || s || '-')
const versionStatusColor = (s) => ({ draft: 'default', reviewing: 'processing', published: 'green', withdrawn: 'orange' }[s] || 'default')
const indexLabel = (s) => ({ pending: '待索引', embedding: '嵌入中', indexed: '已索引', verified: '已验证', ready: '就绪', failed: '失败' }[s] || s || '-')
const indexColor = (s) => ({ pending: 'default', embedding: 'processing', indexed: 'blue', verified: 'cyan', ready: 'green', failed: 'red' }[s] || 'default')

async function loadDictionary() {
  loading.value = true
  try {
    const res = await knowledgeDictionaryApi.getDictionary(dictId.value)
    dict.value = res && res.data ? res.data : {}
    const allVersions = dict.value.versions || []
    versions.value = allVersions
    // 尽量保留当前选中的版本
    const prevId = currentVersionId.value
    const stillExists = allVersions.some((v) => v.id === prevId)
    if (!stillExists) {
      const active = allVersions.find((v) => v.id === dict.value.active_version_id)
      currentVersionId.value = active ? active.id : allVersions.length ? allVersions[0].id : null
    }
    selectedEntryKeys.value = []
    await loadEntries()
    maybeStartIndexPolling()
  } catch (e) {
    console.error('加载字典详情失败:', e)
    if (isNotFoundError(e)) {
      show404.value = true
    } else {
      loadFailed.value = true
      loadFailedMsg.value = errMsg(e, '加载字典详情失败')
    }
  } finally {
    loading.value = false
  }
}

function onVersionChange() {
  entryPage.value = 1
  selectedEntryKeys.value = []
  entries.value = []
  loadEntries()
  stopIndexPolling()
  maybeStartIndexPolling()
}

// ---------------------------------------------------------------------------
// 版本操作：发布 / 撤回 / 重建索引 / 导出
// ---------------------------------------------------------------------------

async function handlePublish() {
  try {
    await knowledgeDictionaryApi.publishVersion(dictId.value, currentVersionId.value)
    message.success('版本已发布')
    await loadDictionary()
  } catch (e) {
    message.error(errMsg(e, '发布失败'))
  }
}

function handleWithdraw() {
  Modal.confirm({
    title: '撤回版本',
    content: '撤回后该版本不再作为活动版本，普通用户将无法查看。确定撤回？',
    okType: 'danger',
    async onOk() {
      try {
        await knowledgeDictionaryApi.withdrawVersion(dictId.value, currentVersionId.value)
        message.success('版本已撤回')
        await loadDictionary()
      } catch (e) {
        message.error(errMsg(e, '撤回失败'))
      }
    },
  })
}

async function handleBuildIndex() {
  try {
    const res = await knowledgeDictionaryApi.buildIndex(dictId.value, currentVersionId.value)
    const job = res && res.data ? res.data : {}
    if (job.id) {
      addJobToStorage({
        jobId: job.id,
        dictionaryName: dict.value ? dict.value.name : '',
        ts: Date.now(),
        status: job.status || 'queued',
      })
    }
    message.success('索引任务已创建')
    startIndexPolling()
  } catch (e) {
    message.error(errMsg(e, '创建索引任务失败'))
  }
}

async function handleExport(format) {
  try {
    const filename = await downloadDictionaryExport(dictId.value, currentVersionId.value, format)
    message.success(`导出成功：${filename}`)
  } catch (e) {
    message.error(errMsg(e, '导出失败'))
  }
}

function onExportMenu({ key }) {
  handleExport(key)
}

// ---------------------------------------------------------------------------
// 索引状态轮询（3s）
// ---------------------------------------------------------------------------

let indexPollTimer = null

function startIndexPolling() {
  stopIndexPolling()
  indexPollTimer = setInterval(pollIndexStatus, 3000)
}

function stopIndexPolling() {
  if (indexPollTimer) {
    clearInterval(indexPollTimer)
    indexPollTimer = null
  }
}

function maybeStartIndexPolling() {
  const v = currentVersion.value
  if (v && ['pending', 'embedding', 'indexed', 'verified'].includes(v.index_status)) {
    startIndexPolling()
  }
}

async function pollIndexStatus() {
  const v = currentVersion.value
  if (!v || !['pending', 'embedding', 'indexed', 'verified'].includes(v.index_status)) {
    stopIndexPolling()
    return
  }
  try {
    const res = await knowledgeDictionaryApi.indexStatus(dictId.value, v.id)
    const st = res && res.data ? res.data.index_status : null
    if (st && !['pending', 'embedding', 'indexed', 'verified'].includes(st)) {
      stopIndexPolling()
      await loadDictionary()
    } else if (st) {
      v.index_status = st
    }
  } catch (e) {
    console.error('索引状态轮询失败:', e)
  }
}

// ---------------------------------------------------------------------------
// 条目列表
// ---------------------------------------------------------------------------

const entriesLoading = ref(false)
const entries = ref([])
const entryTotal = ref(0)
const entryPage = ref(1)
const entryPageSize = ref(20)
const roKeyword = ref('')
const selectedEntryKeys = ref([])

const entryFilters = reactive({
  category: '',
  reviewStatus: undefined,
  keyword: '',
  sourceFile: '',
  minConfidence: null,
  missingFields: false,
  conflictOnly: false,
})

const reviewStatusOptions = [
  { label: '待审核', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已驳回', value: 'rejected' },
  { label: '冲突', value: 'conflict' },
]

const reviewStatusLabel = (s) => ({ pending: '待审核', approved: '已通过', rejected: '已驳回', conflict: '冲突' }[s] || s || '-')
const reviewStatusColor = (s) => ({ pending: 'processing', approved: 'green', rejected: 'red', conflict: 'orange' }[s] || 'default')

function confidenceMeta(c) {
  if (c === null || c === undefined || Number.isNaN(Number(c))) {
    return { color: 'default', text: '-' }
  }
  const v = Number(c)
  if (v < 0.6) return { color: 'red', text: `${(v * 100).toFixed(1)}% 低置信` }
  if (v < 0.85) return { color: 'orange', text: `${(v * 100).toFixed(1)}% 重点审核` }
  return { color: 'green', text: `${(v * 100).toFixed(1)}% 高置信` }
}
const confidenceText = (c) => confidenceMeta(c).text
const confidenceColor = (c) => confidenceMeta(c).color
const confidenceTitle = (c) => {
  if (c === null || c === undefined) return ''
  const v = Number(c)
  if (v < 0.6) return '低置信，建议重点审核'
  if (v < 0.85) return '重点审核'
  return '高置信'
}

const entryColumns = computed(() => {
  const cols = [
    { title: '标准名称', dataIndex: 'standard_name', key: 'standard_name', width: 180 },
    { title: '定义', dataIndex: 'definition', key: 'definition', ellipsis: true },
    { title: '单位', dataIndex: 'unit', key: 'unit', width: 90 },
    { title: '数据类型', dataIndex: 'data_type', key: 'data_type', width: 100 },
    { title: '同义词', key: 'synonyms', width: 200 },
    { title: '取值规则', dataIndex: 'value_rule', key: 'value_rule', ellipsis: true },
  ]
  if (isManager.value) {
    cols.push({ title: '置信度', key: 'confidence', width: 140 })
    cols.push({ title: '审核状态', key: 'review_status', width: 100 })
  }
  if (versionEditable.value) {
    cols.push({ title: '操作', key: 'action', width: 250 })
  }
  return cols
})

const rowSelection = computed(() => {
  if (!versionEditable.value) return undefined
  return {
    selectedRowKeys: selectedEntryKeys.value,
    preserveSelectedRowKeys: true,
    onChange: (keys) => {
      selectedEntryKeys.value = keys
    },
  }
})

async function loadEntries() {
  if (!currentVersionId.value) {
    entries.value = []
    entryTotal.value = 0
    return
  }
  entriesLoading.value = true
  try {
    const params = { page: entryPage.value, pageSize: entryPageSize.value }
    if (versionEditable.value) {
      params.category = entryFilters.category.trim()
      params.reviewStatus = entryFilters.reviewStatus || ''
      params.keyword = entryFilters.keyword.trim()
      params.sourceFile = entryFilters.sourceFile.trim()
      params.minConfidence = entryFilters.minConfidence
      params.missingFields = entryFilters.missingFields
      params.conflictOnly = entryFilters.conflictOnly
    } else {
      params.keyword = roKeyword.value.trim()
    }
    const res = await knowledgeDictionaryApi.listEntries(dictId.value, currentVersionId.value, params)
    const data = res && res.data ? res.data : {}
    entries.value = data.items || []
    entryTotal.value = data.total || 0
  } catch (e) {
    message.error(errMsg(e, '加载条目失败'))
  } finally {
    entriesLoading.value = false
  }
}

function handleEntrySearch() {
  entryPage.value = 1
  loadEntries()
}

function resetEntryFilters() {
  entryFilters.category = ''
  entryFilters.reviewStatus = undefined
  entryFilters.keyword = ''
  entryFilters.sourceFile = ''
  entryFilters.minConfidence = null
  entryFilters.missingFields = false
  entryFilters.conflictOnly = false
  roKeyword.value = ''
  handleEntrySearch()
}

function onEntryPageChange(p) {
  entryPage.value = p
  loadEntries()
}

function onEntrySizeChange(_current, size) {
  entryPageSize.value = size
  entryPage.value = 1
  loadEntries()
}

async function refreshAfterEntriesChange() {
  await loadEntries()
  await loadDictionary()
}

// ---------------------------------------------------------------------------
// 条目审核：单条 / 批量
// ---------------------------------------------------------------------------

const batchAllowLowConfidence = ref(false)

async function reviewEntryAction(record, action) {
  const labels = { approve: '通过', reject: '驳回', reset: '重置' }
  try {
    await knowledgeDictionaryApi.reviewEntry(dictId.value, currentVersionId.value, record.id, { action })
    message.success(`已${labels[action]}`)
    await refreshAfterEntriesChange()
  } catch (e) {
    message.error(errMsg(e, `${labels[action]}失败`))
  }
}

function handleBatchApprove() {
  if (!selectedEntryKeys.value.length) {
    message.warning('请先选择条目')
    return
  }
  const count = selectedEntryKeys.value.length
  const contentNode = h('div', [
    h('p', { style: 'margin-bottom: 8px' }, `确定通过选中的 ${count} 条条目？将通过校验来源证据与必填字段，低置信条目默认不会通过。`),
    h('a-switch', {
      checked: batchAllowLowConfidence.value,
      'onUpdate:checked': (v) => {
        batchAllowLowConfidence.value = v
      },
      checkedChildren: '包含低置信条目',
      unCheckedChildren: '包含低置信条目',
    }),
  ])
  Modal.confirm({
    title: '批量通过',
    content: contentNode,
    async onOk() {
      try {
        const items = selectedEntryKeys.value.map((id) => ({ entry_id: id, action: 'approve' }))
        const res = await knowledgeDictionaryApi.batchReview(dictId.value, currentVersionId.value, {
          items,
          allow_low_confidence: batchAllowLowConfidence.value,
        })
        const data = res && res.data ? res.data : {}
        message.success(`批量通过完成：成功 ${data.succeeded || 0} 条，失败 ${data.failed || 0} 条`)
        await refreshAfterEntriesChange()
      } catch (e) {
        message.error(errMsg(e, '批量通过失败'))
      }
    },
  })
}

function handleBatchReject() {
  if (!selectedEntryKeys.value.length) {
    message.warning('请先选择条目')
    return
  }
  Modal.confirm({
    title: '批量驳回',
    content: `确定驳回选中的 ${selectedEntryKeys.value.length} 条条目？`,
    okType: 'danger',
    async onOk() {
      try {
        const items = selectedEntryKeys.value.map((id) => ({ entry_id: id, action: 'reject' }))
        const res = await knowledgeDictionaryApi.batchReview(dictId.value, currentVersionId.value, { items })
        const data = res && res.data ? res.data : {}
        message.success(`批量驳回完成：成功 ${data.succeeded || 0} 条，失败 ${data.failed || 0} 条`)
        await refreshAfterEntriesChange()
      } catch (e) {
        message.error(errMsg(e, '批量驳回失败'))
      }
    },
  })
}

function handleMergeSelected() {
  const ids = selectedEntryKeys.value
  if (!ids.length) {
    message.warning('请先选择条目')
    return
  }
  if (ids.length < 2) {
    message.warning('合并至少需要 2 条条目')
    return
  }
  const first = entries.value.find((e) => e.id === ids[0])
  Modal.confirm({
    title: '合并所选条目',
    content: `将保留第 1 条「${first ? first.standard_name : ids[0]}」，合并其余 ${ids.length - 1} 条的证据与同义词，合并后回到待审核。确定继续？`,
    async onOk() {
      try {
        const [keep, ...rest] = ids
        await knowledgeDictionaryApi.mergeEntries(dictId.value, currentVersionId.value, {
          keep_entry_id: keep,
          merge_entry_ids: rest,
        })
        message.success('条目已合并')
        await refreshAfterEntriesChange()
      } catch (e) {
        message.error(errMsg(e, '合并失败'))
      }
    },
  })
}

// ---------------------------------------------------------------------------
// 合并建议
// ---------------------------------------------------------------------------

const suggestionsModal = reactive({ open: false, loading: false, items: [], merging: -1 })

async function openMergeSuggestions() {
  suggestionsModal.open = true
  suggestionsModal.loading = true
  suggestionsModal.items = []
  suggestionsModal.merging = -1
  try {
    const res = await knowledgeDictionaryApi.mergeSuggestions(dictId.value, currentVersionId.value, 10)
    suggestionsModal.items = res && res.data ? res.data.items || [] : []
  } catch (e) {
    message.error(errMsg(e, '加载合并建议失败'))
  } finally {
    suggestionsModal.loading = false
  }
}

async function doSuggestionMerge(s, index) {
  suggestionsModal.merging = index
  try {
    await knowledgeDictionaryApi.mergeEntries(dictId.value, currentVersionId.value, {
      keep_entry_id: s.entry_a.id,
      merge_entry_ids: [s.entry_b.id],
    })
    message.success('已合并')
    suggestionsModal.items.splice(index, 1)
    await refreshAfterEntriesChange()
  } catch (e) {
    message.error(errMsg(e, '合并失败'))
  } finally {
    suggestionsModal.merging = -1
  }
}

// ---------------------------------------------------------------------------
// 条目 新建 / 编辑 / 删除
// ---------------------------------------------------------------------------

const dataTypeOptions = ['string', 'number', 'integer', 'boolean', 'date', 'datetime', 'enum', 'range', 'text'].map((v) => ({
  label: v,
  value: v,
}))

const entryEditor = reactive({
  open: false,
  saving: false,
  editingId: null,
  title: '',
  form: {
    category: '',
    standard_name: '',
    definition: '',
    unit: '',
    data_type: undefined,
    synonyms: [],
    value_rule: '',
    evidences: [],
  },
})

function emptyEvidenceRow() {
  return { quote: '', page_no: '', sheet_name: '', cell_range: '' }
}

function addEvidenceRow() {
  entryEditor.form.evidences.push(emptyEvidenceRow())
}

function removeEvidenceRow(i) {
  entryEditor.form.evidences.splice(i, 1)
}

function openEntryCreate() {
  entryEditor.editingId = null
  entryEditor.title = '新建条目'
  Object.assign(entryEditor.form, {
    category: '',
    standard_name: '',
    definition: '',
    unit: '',
    data_type: undefined,
    synonyms: [],
    value_rule: '',
    evidences: [emptyEvidenceRow()],
  })
  entryEditor.open = true
}

async function openEntryEdit(record) {
  entryEditor.editingId = record.id
  entryEditor.title = '编辑条目'
  Object.assign(entryEditor.form, {
    category: record.category || '',
    standard_name: record.standard_name || '',
    definition: record.definition || '',
    unit: record.unit || '',
    data_type: record.data_type || undefined,
    synonyms: record.synonyms || [],
    value_rule: record.value_rule || '',
    evidences: [],
  })
  try {
    const res = await knowledgeDictionaryApi.getEvidences(dictId.value, currentVersionId.value, record.id)
    const items = res && res.data ? res.data.items || [] : []
    entryEditor.form.evidences = items.length
      ? items.map((ev) => ({
          quote: ev.quote || '',
          page_no: ev.page_no || '',
          sheet_name: ev.sheet_name || '',
          cell_range: ev.cell_range || '',
        }))
      : [emptyEvidenceRow()]
  } catch (e) {
    console.error('加载条目证据失败:', e)
    entryEditor.form.evidences = [emptyEvidenceRow()]
  }
  entryEditor.open = true
}

async function saveEntry() {
  const f = entryEditor.form
  if (!f.standard_name.trim()) {
    message.warning('请填写标准名称')
    return
  }
  if (!f.definition.trim()) {
    message.warning('请填写定义')
    return
  }
  const evidences = f.evidences
    .filter((ev) => ev.quote && ev.quote.trim())
    .map((ev) => ({
      quote: ev.quote.trim(),
      page_no: ev.page_no ? String(ev.page_no).trim() : null,
      sheet_name: ev.sheet_name ? String(ev.sheet_name).trim() : null,
      cell_range: ev.cell_range ? String(ev.cell_range).trim() : null,
    }))
  if (!evidences.length) {
    message.warning('请至少填写一条证据引用')
    return
  }
  const payload = {
    category: f.category.trim() || null,
    standard_name: f.standard_name.trim(),
    definition: f.definition.trim(),
    unit: f.unit.trim() || null,
    data_type: f.data_type || null,
    synonyms: f.synonyms && f.synonyms.length ? f.synonyms : null,
    value_rule: f.value_rule.trim() || null,
    evidences,
  }
  entryEditor.saving = true
  try {
    if (entryEditor.editingId) {
      await knowledgeDictionaryApi.updateEntry(dictId.value, currentVersionId.value, entryEditor.editingId, payload)
      message.success('条目已更新')
    } else {
      await knowledgeDictionaryApi.createEntry(dictId.value, currentVersionId.value, payload)
      message.success('条目已创建')
    }
    entryEditor.open = false
    await refreshAfterEntriesChange()
  } catch (e) {
    message.error(errMsg(e, entryEditor.editingId ? '更新失败' : '创建失败'))
  } finally {
    entryEditor.saving = false
  }
}

function handleDeleteEntry(record) {
  Modal.confirm({
    title: '删除条目',
    content: `确定删除条目「${record.standard_name}」？删除后不可恢复。`,
    okType: 'danger',
    async onOk() {
      try {
        await knowledgeDictionaryApi.deleteEntry(dictId.value, currentVersionId.value, record.id)
        message.success('条目已删除')
        await refreshAfterEntriesChange()
      } catch (e) {
        message.error(errMsg(e, '删除失败'))
      }
    },
  })
}

// ---------------------------------------------------------------------------
// 证据抽屉
// ---------------------------------------------------------------------------

const evidenceDrawer = reactive({ open: false, loading: false, items: [], title: '' })

async function openEvidence(record) {
  evidenceDrawer.title = `来源证据：${record.standard_name}`
  evidenceDrawer.open = true
  evidenceDrawer.loading = true
  evidenceDrawer.items = []
  try {
    const res = await knowledgeDictionaryApi.getEvidences(dictId.value, currentVersionId.value, record.id)
    evidenceDrawer.items = res && res.data ? res.data.items || [] : []
  } catch (e) {
    message.error(errMsg(e, '加载证据失败'))
  } finally {
    evidenceDrawer.loading = false
  }
}

// ---------------------------------------------------------------------------
// 只读检索
// ---------------------------------------------------------------------------

const searchQuery = ref('')
const searchLoading = ref(false)
const searchResults = ref([])
const searched = ref(false)

async function doSearch() {
  const q = searchQuery.value.trim()
  if (!q) {
    message.warning('请输入检索关键词')
    return
  }
  searchLoading.value = true
  try {
    const res = await knowledgeDictionaryApi.search({ query: q, dictionary_ids: [dictId.value], top_k: 10 })
    searchResults.value = res && res.data ? res.data.items || [] : []
    searched.value = true
  } catch (e) {
    message.error(errMsg(e, '检索失败'))
  } finally {
    searchLoading.value = false
  }
}

function similarityPercent(s) {
  if (typeof s !== 'number' || Number.isNaN(s)) return 0
  // Milvus COSINE：新版本返回距离(0~2)，旧版本返回相似度(-1~1)，统一归一化为 0~100
  const v = s >= 0 && s <= 1 ? s : Math.max(0, 1 - s)
  return Math.max(0, Math.min(100, Math.round(v * 100)))
}

function similarityColor(s) {
  const p = similarityPercent(s)
  if (p >= 85) return 'green'
  if (p >= 60) return 'orange'
  return 'red'
}

// ---------------------------------------------------------------------------
// 任务写入 localStorage（供列表页任务面板恢复；key 与列表页一致）
// ---------------------------------------------------------------------------

const JOB_KEY = 'kd_last_jobs'

function addJobToStorage(job) {
  try {
    const arr = JSON.parse(localStorage.getItem(JOB_KEY) || '[]')
    const list = Array.isArray(arr) ? arr : []
    const next = [job, ...list.filter((j) => j.jobId !== job.jobId)]
    localStorage.setItem(JOB_KEY, JSON.stringify(next))
  } catch (e) {
    console.error('保存任务列表失败:', e)
  }
}

onMounted(() => {
  loadDictionary()
})

onBeforeUnmount(() => {
  stopIndexPolling()
})
</script>

<style lang="less" scoped>
.kd-detail {
  min-height: 100%;
}

// 顶部操作按钮：图标与文字垂直居中对齐（lucide SVG 默认行内基线会错位）
.kd-detail :deep(.ant-btn) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.kd-detail :deep(.ant-btn .anticon) {
  display: inline-flex;
  align-items: center;
  line-height: 1;
}

.kd-detail :deep(.ant-btn .anticon svg) {
  display: block;
  vertical-align: middle;
}

.kd-detail-body {
  padding: 16px 24px;
}

.dict-desc {
  margin-bottom: 16px;
}

.section-block {
  margin-bottom: 24px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.version-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.version-item {
  display: block;
  width: 100%;
  margin-right: 0;
  padding: 8px 12px;
  border: 1px solid var(--gray-300);
  border-radius: 8px;
  background: var(--surface-raised);
  transition: border-color 0.2s;

  &:hover {
    border-color: var(--main-color);
  }
}

.version-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.version-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 13px;
}

.v-no {
  font-weight: 700;
  color: var(--text-primary);
}

.version-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.entries-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.entries-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.entry-name {
  font-weight: 500;
  color: var(--text-primary);
}

.ellipsis-text {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.muted {
  color: var(--gray-600);
}

.search-result {
  border: 1px solid var(--gray-300);
  border-radius: 8px;
  padding: 12px;
  margin-top: 12px;
  background: var(--surface-raised);
}

.search-result-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.search-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-primary);
}

.search-def {
  margin-top: 6px;
  color: var(--gray-800);
  font-size: 13px;
}

.search-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin-top: 6px;
  font-size: 12px;
}

.search-evidence {
  margin-top: 8px;
  border-top: 1px dashed var(--gray-300);
  padding-top: 8px;
}

.search-evidence-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 6px;
  font-size: 12px;
}

.quote {
  color: var(--gray-800);
}

.evidence-card {
  border: 1px solid var(--gray-300);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  background: var(--surface-raised);
}

.evidence-quote {
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--gray-100);
  border-radius: 6px;
  padding: 8px;
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--text-primary);
}

.evidence-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
}

.evidence-loc {
  display: flex;
  gap: 12px;
  margin-top: 6px;
  font-size: 12px;
  flex-wrap: wrap;
}

.evidence-edit-row {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;

  .ev-quote {
    flex: 2;
    min-width: 160px;
  }

  .ev-loc {
    flex: 1;
    min-width: 90px;
  }
}

.suggestion-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border: 1px solid var(--gray-300);
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 8px;
}

.suggestion-names {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.suggestion-name {
  font-weight: 500;
  color: var(--text-primary);
}
</style>
