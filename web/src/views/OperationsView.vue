<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  PlusOutlined,
  ReloadOutlined,
  SyncOutlined,
  DownloadOutlined,
  SafetyCertificateOutlined,
  RollbackOutlined,
  SendOutlined,
} from '@ant-design/icons-vue'
import {
  monitoringApi,
  auditApi,
  backupApi,
  configHistoryApi,
  alertApi,
  downloadAuthenticated,
} from '@/apis/local_features'

const activeTab = ref('health')

// ---------------------------------------------------------------------------
// 运行状态：本机健康检查 + 全量依赖检查
// ---------------------------------------------------------------------------
const healthLoading = ref(false)
const healthData = ref({ status: 'unavailable', checks: {} })
const depsLoading = ref(false)
const depsData = ref({ status: 'unavailable', dependencies: {} })

const loadHealth = async (withDeps = false) => {
  if (withDeps) {
    depsLoading.value = true
    try {
      const res = await monitoringApi.dependencies()
      if (res && res.status === 'success' && res.data) depsData.value = res.data
    } catch (e) {
      message.error('依赖检查失败: ' + (e.message || '未知错误'))
    } finally {
      depsLoading.value = false
    }
    return
  }
  healthLoading.value = true
  try {
    const res = await monitoringApi.health()
    if (res && res.status === 'success' && res.data) healthData.value = res.data
  } catch (e) {
    message.error('健康检查失败: ' + (e.message || '未知错误'))
  } finally {
    healthLoading.value = false
  }
}

const formatBytes = (n) => {
  if (n === null || n === undefined) return '-'
  if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB'
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n >= 1024) return (n / 1024).toFixed(1) + ' KB'
  return n + ' B'
}

const statusColor = (s) =>
  ({ ok: 'green', degraded: 'orange', failed: 'red', firing: 'red', timeout: 'orange', unavailable: 'default' }[s] || 'default')
const statusLabel = (s) =>
  ({ ok: '正常', degraded: '降级', failed: '失败', firing: '触发中', timeout: '超时', unavailable: '不可用' }[s] || s || '-')

const checkLabel = (name) =>
  ({
    api: 'API 服务',
    sqlite: 'SQLite 数据库',
    disk: '磁盘空间',
    backup_dir: '备份目录',
    milvus: 'Milvus',
    neo4j: 'Neo4j',
    gpu: 'GPU',
    last_backup: '最近备份',
    last_alert: '最近告警',
  }[name] || name)

const checkDetail = (check) => {
  if (!check) return '-'
  if (check.detail) return check.detail
  const parts = []
  if (check.used_percent !== undefined) parts.push(`已用 ${check.used_percent}%`)
  if (check.free_bytes !== undefined) parts.push(`剩余 ${formatBytes(check.free_bytes)}`)
  if (check.utilization_percent !== undefined) parts.push(`利用率 ${check.utilization_percent}%`)
  if (check.vram_used_percent !== undefined) parts.push(`显存 ${check.vram_used_percent}%`)
  if (check.size_bytes !== undefined) parts.push(`大小 ${formatBytes(check.size_bytes)}`)
  if (check.job_status) parts.push(`状态 ${check.job_status}`)
  if (check.alert_status) parts.push(`状态 ${check.alert_status}`)
  if (check.message) parts.push(check.message)
  if (check.created_at) parts.push(check.created_at)
  return parts.length ? parts.join('；') : '-'
}

// ---------------------------------------------------------------------------
// 审计日志：服务器分页 + 筛选
// ---------------------------------------------------------------------------
const auditLoading = ref(false)
const auditItems = ref([])
const auditTotal = ref(0)
const auditPage = ref(1)
const auditPageSize = ref(20)
const auditFilters = reactive({ user: '', action: '', status: '' })

const loadAudit = async () => {
  auditLoading.value = true
  try {
    const res = await auditApi.events({
      user: auditFilters.user,
      action: auditFilters.action,
      status: auditFilters.status,
      page: auditPage.value,
      page_size: auditPageSize.value,
    })
    if (res && res.status === 'success' && res.data) {
      auditItems.value = res.data.items || []
      auditTotal.value = res.data.total || 0
    }
  } catch (e) {
    message.error('审计日志加载失败: ' + (e.message || '未知错误'))
  } finally {
    auditLoading.value = false
  }
}

const auditColumns = [
  { title: 'ID', dataIndex: 'id', key: 'id', width: 80 },
  { title: '用户名', dataIndex: 'username', key: 'username', width: 120 },
  { title: '动作', dataIndex: 'action', key: 'action' },
  { title: '状态', dataIndex: 'status', key: 'status', width: 90 },
  { title: '资源类型', dataIndex: 'resource_type', key: 'resource_type', width: 120 },
  { title: '资源 ID', dataIndex: 'resource_id', key: 'resource_id', width: 110 },
  { title: 'IP', dataIndex: 'ip_address', key: 'ip_address', width: 130 },
  { title: '时间', dataIndex: 'timestamp', key: 'timestamp', width: 180 },
  { title: '操作', key: 'action_btn', width: 80 },
]

const auditDetail = ref(null)
const auditDetailVisible = ref(false)
const openAuditDetail = (record) => {
  auditDetail.value = record
  auditDetailVisible.value = true
}

// ---------------------------------------------------------------------------
// 备份恢复：服务器分页；恢复前先预检摘要 + 二次确认
// ---------------------------------------------------------------------------
const backupsLoading = ref(false)
const backups = ref([])
const backupTotal = ref(0)
const backupPage = ref(1)
const backupPageSize = ref(10)
const backupModalVisible = ref(false)
const backupCreating = ref(false)
const backupForm = reactive({ include_kb: false, include_logs: true, note: '' })

const loadBackups = async () => {
  backupsLoading.value = true
  try {
    const res = await backupApi.list(backupPage.value, backupPageSize.value)
    if (res && res.status === 'success' && res.data) {
      backups.value = res.data.items || []
      backupTotal.value = res.data.total || 0
    }
  } catch (e) {
    message.error('备份列表加载失败: ' + (e.message || '未知错误'))
  } finally {
    backupsLoading.value = false
  }
}

const openBackupCreate = () => {
  Object.assign(backupForm, { include_kb: false, include_logs: true, note: '' })
  backupModalVisible.value = true
}

const doCreateBackup = async () => {
  backupCreating.value = true
  try {
    await backupApi.create(backupForm)
    message.success('备份已创建')
    backupModalVisible.value = false
    await loadBackups()
  } catch (e) {
    message.error(e.message || '创建备份失败')
  } finally {
    backupCreating.value = false
  }
}

const backupStatusColor = (s) => ({ completed: 'green', running: 'blue', failed: 'red' }[s] || 'default')
const backupStatusLabel = (s) => ({ completed: '已完成', running: '进行中', failed: '失败' }[s] || s || '-')

const backupColumns = [
  { title: '文件名', dataIndex: 'filename', key: 'filename' },
  { title: '大小', dataIndex: 'size_bytes', key: 'size_bytes', width: 110 },
  { title: '状态', dataIndex: 'status', key: 'status', width: 90 },
  { title: '创建人', dataIndex: 'created_by', key: 'created_by', width: 100 },
  { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 170 },
  { title: '备注', dataIndex: 'note', key: 'note', width: 140 },
  { title: '操作', key: 'action', width: 280 },
]

const downloadBackup = async (record) => {
  const res = await downloadAuthenticated(
    backupApi.downloadUrl(record.id),
    record.filename || `backup_${record.id}.zip`,
  )
  if (!res.ok) message.error(res.message || '下载失败')
}

const verifyBackup = async (record) => {
  try {
    const res = await backupApi.verify(record.id)
    if (res && res.status === 'success') {
      message.success('备份校验通过（SHA-256 与条目一致）')
      await loadBackups()
    }
  } catch (e) {
    message.error(e.message || '校验失败')
  }
}

const restorePreviewVisible = ref(false)
const restorePreviewLoading = ref(false)
const restorePreview = ref(null)
const restoreBackupId = ref(null)
const restoring = ref(false)

const openRestorePreview = async (record) => {
  restorePreviewLoading.value = true
  restorePreview.value = null
  restoreBackupId.value = record.id
  restorePreviewVisible.value = true
  try {
    const res = await backupApi.preview(record.id)
    if (res && res.status === 'success' && res.data) restorePreview.value = res.data
  } catch (e) {
    message.error(e.message || '恢复预检失败')
    restorePreviewVisible.value = false
  } finally {
    restorePreviewLoading.value = false
  }
}

const doRestore = () => {
  if (!restorePreview.value || !restorePreview.value.token) {
    message.warning('请先执行恢复预检')
    return
  }
  // 二次确认：预检摘要已展示后，仍需危险确认
  Modal.confirm({
    title: '确认恢复备份',
    content:
      '将使用该备份覆盖当前数据库、配置与日志。系统会先自动创建恢复点；此操作不可撤销，确认继续？',
    okType: 'danger',
    okText: '确认恢复',
    async onOk() {
      restoring.value = true
      try {
        const res = await backupApi.restore(restoreBackupId.value, restorePreview.value.token)
        if (res && res.status === 'success') {
          message.success('恢复完成，刷新后生效')
          restorePreviewVisible.value = false
          await loadBackups()
        }
      } catch (e) {
        message.error(e.message || '恢复失败')
      } finally {
        restoring.value = false
      }
    },
  })
}

const deleteBackup = (record) => {
  Modal.confirm({
    title: '删除备份',
    content: `确定删除备份「${record.filename}」？不可恢复。`,
    okType: 'danger',
    async onOk() {
      try {
        await backupApi.remove(record.id)
        message.success('已删除')
        await loadBackups()
      } catch (e) {
        message.error(e.message || '删除失败')
      }
    },
  })
}

// ---------------------------------------------------------------------------
// 配置历史：服务器分页 + 回滚
// ---------------------------------------------------------------------------
const historyLoading = ref(false)
const historyItems = ref([])
const historyTotal = ref(0)
const historyPage = ref(1)
const historyPageSize = ref(10)
const historyOperator = ref('')

const loadHistory = async () => {
  historyLoading.value = true
  try {
    const res = await configHistoryApi.history({
      operator: historyOperator.value,
      page: historyPage.value,
      page_size: historyPageSize.value,
    })
    if (res && res.status === 'success' && res.data) {
      historyItems.value = res.data.items || []
      historyTotal.value = res.data.total || 0
    }
  } catch (e) {
    message.error('配置历史加载失败: ' + (e.message || '未知错误'))
  } finally {
    historyLoading.value = false
  }
}

const changesText = (changes) => {
  if (!Array.isArray(changes) || !changes.length) return '-'
  return changes.map((c) => `${c.key}: ${c.old ?? '-'} → ${c.new ?? '-'}`).join('；')
}

const historyColumns = [
  { title: 'ID', dataIndex: 'id', key: 'id', width: 70 },
  { title: '操作人', dataIndex: 'operator', key: 'operator', width: 110 },
  { title: '变更项', key: 'changes', width: 260 },
  { title: '说明', dataIndex: 'description', key: 'description' },
  { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 170 },
  { title: '操作', key: 'action', width: 90 },
]

const rollbackModalVisible = ref(false)
const rollbackTarget = ref(null)
const rollbackDesc = ref('')
const rollbacking = ref(false)

const openRollback = (record) => {
  rollbackTarget.value = record
  rollbackDesc.value = ''
  rollbackModalVisible.value = true
}

const doRollback = async () => {
  rollbacking.value = true
  try {
    await configHistoryApi.rollback(rollbackTarget.value.id, rollbackDesc.value || null)
    message.success('配置已回滚')
    rollbackModalVisible.value = false
    await loadHistory()
  } catch (e) {
    message.error(e.message || '回滚失败')
  } finally {
    rollbacking.value = false
  }
}

// ---------------------------------------------------------------------------
// 告警规则（无分页，规则数量有限）+ 告警事件（服务器分页）+ SMTP 测试
// ---------------------------------------------------------------------------
const RULE_TYPES = [
  { value: 'disk_space', label: '磁盘剩余比例' },
  { value: 'sqlite_check', label: 'SQLite 检查失败' },
  { value: 'milvus', label: 'Milvus 不可用' },
  { value: 'neo4j', label: 'Neo4j 不可用' },
  { value: 'gpu_mem', label: 'GPU 显存使用率' },
  { value: 'backup_fail', label: '备份连续失败' },
]

const rulesLoading = ref(false)
const rules = ref([])
const ruleModalVisible = ref(false)
const ruleSaving = ref(false)
const editingRule = ref(null)
const ruleForm = reactive({
  name: '',
  rule_type: 'disk_space',
  enabled: true,
  threshold: '',
  cooldown_seconds: 3600,
  notify_email: '',
})

const loadRules = async () => {
  rulesLoading.value = true
  try {
    const res = await alertApi.rules()
    if (res && res.status === 'success' && res.data) rules.value = res.data.items || []
  } catch (e) {
    message.error('告警规则加载失败: ' + (e.message || '未知错误'))
  } finally {
    rulesLoading.value = false
  }
}

const openRuleCreate = () => {
  editingRule.value = null
  Object.assign(ruleForm, {
    name: '',
    rule_type: 'disk_space',
    enabled: true,
    threshold: '',
    cooldown_seconds: 3600,
    notify_email: '',
  })
  ruleModalVisible.value = true
}

const openRuleEdit = (record) => {
  editingRule.value = record
  Object.assign(ruleForm, {
    name: record.name || '',
    rule_type: record.rule_type || 'disk_space',
    enabled: !!record.enabled,
    threshold: record.threshold ?? '',
    cooldown_seconds: record.cooldown_seconds ?? 3600,
    notify_email: record.notify_email || '',
  })
  ruleModalVisible.value = true
}

const saveRule = async () => {
  if (!ruleForm.name || !ruleForm.name.trim()) {
    message.warning('规则名称不能为空')
    return
  }
  ruleSaving.value = true
  try {
    const payload = {
      name: ruleForm.name.trim(),
      rule_type: ruleForm.rule_type,
      enabled: !!ruleForm.enabled,
      threshold: ruleForm.threshold === '' ? null : ruleForm.threshold,
      cooldown_seconds: Number(ruleForm.cooldown_seconds),
      notify_email: ruleForm.notify_email || null,
    }
    if (editingRule.value) {
      await alertApi.updateRule(editingRule.value.id, payload)
      message.success('规则已更新')
    } else {
      await alertApi.createRule(payload)
      message.success('规则已创建')
    }
    ruleModalVisible.value = false
    await loadRules()
  } catch (e) {
    message.error(e.message || '保存失败')
  } finally {
    ruleSaving.value = false
  }
}

const deleteRule = (record) => {
  Modal.confirm({
    title: '删除告警规则',
    content: `确定删除规则「${record.name}」？历史告警事件将保留。`,
    okType: 'danger',
    async onOk() {
      try {
        await alertApi.deleteRule(record.id)
        message.success('已删除')
        await loadRules()
      } catch (e) {
        message.error(e.message || '删除失败')
      }
    },
  })
}

const ruleColumns = [
  { title: '名称', dataIndex: 'name', key: 'name' },
  { title: '类型', dataIndex: 'rule_type_label', key: 'rule_type_label', width: 150 },
  { title: '启用', dataIndex: 'enabled', key: 'enabled', width: 80 },
  { title: '阈值', dataIndex: 'threshold', key: 'threshold', width: 90 },
  { title: '冷却(秒)', dataIndex: 'cooldown_seconds', key: 'cooldown_seconds', width: 100 },
  { title: '通知邮箱', dataIndex: 'notify_email', key: 'notify_email', width: 200 },
  { title: '创建人', dataIndex: 'created_by', key: 'created_by', width: 100 },
  { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 170 },
  { title: '操作', key: 'action', width: 130 },
]

const eventsLoading = ref(false)
const events = ref([])
const eventsTotal = ref(0)
const eventsPage = ref(1)
const eventsPageSize = ref(10)
const eventStatus = ref('')

const loadEvents = async () => {
  eventsLoading.value = true
  try {
    const res = await alertApi.events({
      status: eventStatus.value,
      page: eventsPage.value,
      page_size: eventsPageSize.value,
    })
    if (res && res.status === 'success' && res.data) {
      events.value = res.data.items || []
      eventsTotal.value = res.data.total || 0
    }
  } catch (e) {
    message.error('告警事件加载失败: ' + (e.message || '未知错误'))
  } finally {
    eventsLoading.value = false
  }
}

const eventStatusColor = (s) => ({ firing: 'red', acknowledged: 'orange', resolved: 'green' }[s] || 'default')
const eventStatusLabel = (s) => ({ firing: '触发中', acknowledged: '已确认', resolved: '已恢复' }[s] || s || '-')
const severityColor = (s) => ({ warning: 'orange', error: 'red', critical: 'red', info: 'blue' }[s] || 'default')

const acknowledgeEvent = async (record) => {
  try {
    await alertApi.acknowledge(record.id)
    message.success('已确认')
    await loadEvents()
  } catch (e) {
    message.error(e.message || '确认失败')
  }
}

const eventColumns = [
  { title: 'ID', dataIndex: 'id', key: 'id', width: 70 },
  { title: '规则', dataIndex: 'rule_id', key: 'rule_id', width: 80 },
  { title: '事件类型', dataIndex: 'event_type', key: 'event_type', width: 100 },
  { title: '级别', dataIndex: 'severity', key: 'severity', width: 90 },
  { title: '状态', dataIndex: 'status', key: 'status', width: 90 },
  { title: '消息', dataIndex: 'message', key: 'message' },
  { title: '触发时间', dataIndex: 'created_at', key: 'created_at', width: 170 },
  { title: '操作', key: 'action', width: 90 },
]

const testEmailVisible = ref(false)
const testEmailAddr = ref('')
const emailSending = ref(false)

const openTestEmail = () => {
  testEmailAddr.value = ''
  testEmailVisible.value = true
}

const sendTestEmail = async () => {
  if (!testEmailAddr.value || !testEmailAddr.value.trim()) {
    message.warning('请输入收件人邮箱')
    return
  }
  emailSending.value = true
  try {
    await alertApi.testEmail(testEmailAddr.value.trim())
    message.success('测试邮件已发送')
    testEmailVisible.value = false
  } catch (e) {
    message.error(e.message || '发送失败')
  } finally {
    emailSending.value = false
  }
}

onMounted(() => {
  loadHealth()
  loadAudit()
  loadBackups()
  loadHistory()
  loadRules()
  loadEvents()
})
</script>

<template>
  <div class="operations-view">
    <a-tabs v-model:activeKey="activeTab">
      <!-- 运行状态 -->
      <a-tab-pane key="health" tab="运行状态">
        <div class="ops-toolbar">
          <a-button type="primary" @click="loadHealth(false)">
            <template #icon><ReloadOutlined /></template>
            刷新
          </a-button>
          <a-button :loading="depsLoading" @click="loadHealth(true)">
            <template #icon><SyncOutlined /></template>
            全量依赖检查
          </a-button>
        </div>

        <div class="status-block">
          <div class="status-head">
            <span class="status-title">本机服务健康</span>
            <a-tag :color="statusColor(healthData.status)">{{ statusLabel(healthData.status) }}</a-tag>
          </div>
          <template v-if="!healthLoading">
            <div class="status-row" v-for="(check, name) in healthData.checks || {}" :key="`h-${name}`">
              <span class="status-name">{{ checkLabel(name) }}</span>
              <a-tag :color="statusColor(check.status)">{{ statusLabel(check.status) }}</a-tag>
              <span class="status-detail">{{ checkDetail(check) }}</span>
            </div>
          </template>
          <div v-else class="status-loading">正在检查…</div>
        </div>

        <div v-if="Object.keys(depsData.dependencies || {}).length" class="status-block">
          <div class="status-head">
            <span class="status-title">依赖检查（每项独立超时，单项失败不影响其它项）</span>
            <a-tag :color="statusColor(depsData.status)">{{ statusLabel(depsData.status) }}</a-tag>
          </div>
          <div class="status-row" v-for="(check, name) in depsData.dependencies" :key="`d-${name}`">
            <span class="status-name">{{ checkLabel(name) }}</span>
            <a-tag :color="statusColor(check.status)">{{ statusLabel(check.status) }}</a-tag>
            <span class="status-detail">{{ checkDetail(check) }}</span>
          </div>
        </div>
      </a-tab-pane>

      <!-- 审计日志 -->
      <a-tab-pane key="audit" tab="审计日志">
        <div class="ops-toolbar">
          <a-input
            v-model:value="auditFilters.user"
            placeholder="用户名"
            allow-clear
            style="width: 150px"
            @press-enter="() => { auditPage = 1; loadAudit() }"
          />
          <a-input
            v-model:value="auditFilters.action"
            placeholder="动作码，如 backup.create"
            allow-clear
            style="width: 200px"
            @press-enter="() => { auditPage = 1; loadAudit() }"
          />
          <a-select v-model:value="auditFilters.status" placeholder="状态" allow-clear style="width: 110px">
            <a-select-option value="success">成功</a-select-option>
            <a-select-option value="failed">失败</a-select-option>
          </a-select>
          <a-button type="primary" @click="() => { auditPage = 1; loadAudit() }">查询</a-button>
          <a-button
            @click="
              () => {
                auditFilters.user = ''
                auditFilters.action = ''
                auditFilters.status = ''
                auditPage = 1
                loadAudit()
              }
            "
          >重置</a-button>
        </div>

        <a-table
          :columns="auditColumns"
          :data-source="auditItems"
          row-key="id"
          :loading="auditLoading"
          size="middle"
          bordered
          :pagination="{
            current: auditPage,
            pageSize: auditPageSize,
            total: auditTotal,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条审计`,
            onChange: (page, size) => { auditPage = page; auditPageSize = size; loadAudit() },
          }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'status'">
              <a-tag :color="record.status === 'success' ? 'green' : 'red'">{{ record.status }}</a-tag>
            </template>
            <template v-else-if="column.key === 'action_btn'">
              <a-button type="link" @click="openAuditDetail(record)">详情</a-button>
            </template>
          </template>
        </a-table>
      </a-tab-pane>

      <!-- 备份恢复 -->
      <a-tab-pane key="backup" tab="备份恢复">
        <div class="ops-toolbar">
          <a-button type="primary" @click="openBackupCreate">
            <template #icon><PlusOutlined /></template>
            新建备份
          </a-button>
          <a-button @click="loadBackups">
            <template #icon><ReloadOutlined /></template>
            刷新
          </a-button>
        </div>

        <a-table
          :columns="backupColumns"
          :data-source="backups"
          row-key="id"
          :loading="backupsLoading"
          size="middle"
          bordered
          :pagination="{
            current: backupPage,
            pageSize: backupPageSize,
            total: backupTotal,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 个备份`,
            onChange: (page, size) => { backupPage = page; backupPageSize = size; loadBackups() },
          }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'size_bytes'">
              {{ formatBytes(record.size_bytes) }}
            </template>
            <template v-else-if="column.key === 'status'">
              <a-tag :color="backupStatusColor(record.status)">{{ backupStatusLabel(record.status) }}</a-tag>
            </template>
            <template v-else-if="column.key === 'note'">
              <span v-if="record.note">{{ record.note }}</span>
              <span v-else class="muted">-</span>
            </template>
            <template v-else-if="column.key === 'action'">
              <a-button type="link" @click="downloadBackup(record)">
                <template #icon><DownloadOutlined /></template>
                下载
              </a-button>
              <a-button type="link" @click="verifyBackup(record)">
                <template #icon><SafetyCertificateOutlined /></template>
                校验
              </a-button>
              <a-button type="link" @click="openRestorePreview(record)">预检恢复</a-button>
              <a-button type="link" danger @click="deleteBackup(record)">删除</a-button>
            </template>
          </template>
        </a-table>
      </a-tab-pane>

      <!-- 配置历史 -->
      <a-tab-pane key="config" tab="配置历史">
        <div class="ops-toolbar">
          <a-input
            v-model:value="historyOperator"
            placeholder="操作人"
            allow-clear
            style="width: 150px"
            @press-enter="() => { historyPage = 1; loadHistory() }"
          />
          <a-button type="primary" @click="() => { historyPage = 1; loadHistory() }">查询</a-button>
          <a-button @click="() => { historyOperator = ''; historyPage = 1; loadHistory() }">重置</a-button>
        </div>

        <a-table
          :columns="historyColumns"
          :data-source="historyItems"
          row-key="id"
          :loading="historyLoading"
          size="middle"
          bordered
          :pagination="{
            current: historyPage,
            pageSize: historyPageSize,
            total: historyTotal,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条变更`,
            onChange: (page, size) => { historyPage = page; historyPageSize = size; loadHistory() },
          }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'changes'">
              <span class="change-text">{{ changesText(record.changes) }}</span>
            </template>
            <template v-else-if="column.key === 'description'">
              <span v-if="record.description">{{ record.description }}</span>
              <span v-else class="muted">-</span>
            </template>
            <template v-else-if="column.key === 'action'">
              <a-button type="link" @click="openRollback(record)">
                <template #icon><RollbackOutlined /></template>
                回滚
              </a-button>
            </template>
          </template>
        </a-table>
      </a-tab-pane>

      <!-- 告警规则 -->
      <a-tab-pane key="alert" tab="告警规则">
        <div class="ops-toolbar">
          <a-button type="primary" @click="openRuleCreate">
            <template #icon><PlusOutlined /></template>
            新建规则
          </a-button>
          <a-button @click="loadRules">
            <template #icon><ReloadOutlined /></template>
            刷新规则
          </a-button>
          <a-divider type="vertical" />
          <a-button @click="openTestEmail">
            <template #icon><SendOutlined /></template>
            发送测试邮件
          </a-button>
        </div>

        <a-table
          :columns="ruleColumns"
          :data-source="rules"
          row-key="id"
          :loading="rulesLoading"
          size="middle"
          bordered
          :pagination="false"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'enabled'">
              <a-tag :color="record.enabled ? 'green' : 'default'">{{ record.enabled ? '启用' : '停用' }}</a-tag>
            </template>
            <template v-else-if="column.key === 'threshold'">
              <span v-if="record.threshold != null">{{ record.threshold }}</span>
              <span v-else class="muted">-</span>
            </template>
            <template v-else-if="column.key === 'notify_email'">
              <span v-if="record.notify_email">{{ record.notify_email }}</span>
              <span v-else class="muted">-</span>
            </template>
            <template v-else-if="column.key === 'action'">
              <a-button type="link" @click="openRuleEdit(record)">编辑</a-button>
              <a-button type="link" danger @click="deleteRule(record)">删除</a-button>
            </template>
          </template>
        </a-table>

        <div class="ops-toolbar event-toolbar">
          <a-select v-model:value="eventStatus" placeholder="事件状态" allow-clear style="width: 130px">
            <a-select-option value="firing">触发中</a-select-option>
            <a-select-option value="acknowledged">已确认</a-select-option>
            <a-select-option value="resolved">已恢复</a-select-option>
          </a-select>
          <a-button type="primary" @click="() => { eventsPage = 1; loadEvents() }">查询事件</a-button>
          <a-button @click="() => { eventStatus = ''; eventsPage = 1; loadEvents() }">重置</a-button>
        </div>

        <a-table
          :columns="eventColumns"
          :data-source="events"
          row-key="id"
          :loading="eventsLoading"
          size="middle"
          bordered
          :pagination="{
            current: eventsPage,
            pageSize: eventsPageSize,
            total: eventsTotal,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条事件`,
            onChange: (page, size) => { eventsPage = page; eventsPageSize = size; loadEvents() },
          }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'severity'">
              <a-tag :color="severityColor(record.severity)">{{ record.severity }}</a-tag>
            </template>
            <template v-else-if="column.key === 'status'">
              <a-tag :color="eventStatusColor(record.status)">{{ eventStatusLabel(record.status) }}</a-tag>
            </template>
            <template v-else-if="column.key === 'action'">
              <a-button v-if="record.status === 'firing'" type="link" @click="acknowledgeEvent(record)">确认</a-button>
              <span v-else class="muted">-</span>
            </template>
          </template>
        </a-table>
      </a-tab-pane>
    </a-tabs>

    <!-- 审计详情抽屉 -->
    <a-drawer v-model:open="auditDetailVisible" :title="`审计详情 #${auditDetail?.id ?? ''}`" width="520px">
      <a-descriptions v-if="auditDetail" :column="1" size="small" bordered>
        <a-descriptions-item label="动作">{{ auditDetail.action }}</a-descriptions-item>
        <a-descriptions-item label="用户名">{{ auditDetail.username || '-' }}</a-descriptions-item>
        <a-descriptions-item label="状态">{{ auditDetail.status }}</a-descriptions-item>
        <a-descriptions-item label="资源类型">{{ auditDetail.resource_type || '-' }}</a-descriptions-item>
        <a-descriptions-item label="资源 ID">{{ auditDetail.resource_id || '-' }}</a-descriptions-item>
        <a-descriptions-item label="IP">{{ auditDetail.ip_address || '-' }}</a-descriptions-item>
        <a-descriptions-item label="时间">{{ auditDetail.timestamp || '-' }}</a-descriptions-item>
        <a-descriptions-item label="详细信息">
          <pre class="detail-json">{{ JSON.stringify(auditDetail.details || {}, null, 2) }}</pre>
        </a-descriptions-item>
      </a-descriptions>
    </a-drawer>

    <!-- 新建备份 -->
    <a-modal
      v-model:open="backupModalVisible"
      title="新建备份"
      :confirm-loading="backupCreating"
      @ok="doCreateBackup"
    >
      <a-form :model="backupForm" layout="vertical">
        <a-form-item label="包含知识源文件">
          <a-switch v-model:checked="backupForm.include_kb" checked-children="包含" un-checked-children="不包含" />
          <div class="form-hint">仅在备份目录 knowledge/ 下存在源文件时启用，归档体积会更大。</div>
        </a-form-item>
        <a-form-item label="包含日志">
          <a-switch v-model:checked="backupForm.include_logs" checked-children="包含" un-checked-children="不包含" />
        </a-form-item>
        <a-form-item label="备注">
          <a-input v-model:value="backupForm.note" maxlength="255" placeholder="可选" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 恢复预检摘要 + 二次确认 -->
    <a-modal
      v-model:open="restorePreviewVisible"
      title="恢复预检"
      :footer="null"
      :mask-closable="false"
    >
      <a-spin :spinning="restorePreviewLoading">
        <template v-if="restorePreview">
          <a-alert
            type="warning"
            :message="`将使用备份 #${restorePreview.backup_id}（创建于 ${restorePreview.created_at || '-'}）覆盖当前数据`"
            style="margin-bottom: 12px"
          />
          <div class="preview-section">
            <div class="preview-line"><a-tag color="green">新增 {{ restorePreview.added?.length || 0 }}</a-tag><span class="preview-files">{{ (restorePreview.added || []).join('、') || '无' }}</span></div>
            <div class="preview-line"><a-tag color="orange">覆盖 {{ restorePreview.overwritten?.length || 0 }}</a-tag><span class="preview-files">{{ (restorePreview.overwritten || []).join('、') || '无' }}</span></div>
            <div class="preview-line"><a-tag>跳过 {{ restorePreview.skipped?.length || 0 }}</a-tag><span class="preview-files">{{ (restorePreview.skipped || []).join('、') || '无' }}</span></div>
          </div>
          <div class="ops-footer">
            <a-button @click="restorePreviewVisible = false">取消</a-button>
            <a-button type="danger" :loading="restoring" @click="doRestore">确认恢复</a-button>
          </div>
        </template>
      </a-spin>
    </a-modal>

    <!-- 配置回滚 -->
    <a-modal
      v-model:open="rollbackModalVisible"
      title="回滚配置"
      :confirm-loading="rollbacking"
      @ok="doRollback"
    >
      <a-alert
        v-if="rollbackTarget"
        type="warning"
        :message="`将回滚到变更 #${rollbackTarget.id} 之前的配置，涉及：${changesText(rollbackTarget.changes)}`"
        style="margin-bottom: 12px"
      />
      <a-form layout="vertical">
        <a-form-item label="回滚说明（可选）">
          <a-input v-model:value="rollbackDesc" maxlength="255" placeholder="如：误改参数，恢复原值" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 新建/编辑告警规则 -->
    <a-modal
      v-model:open="ruleModalVisible"
      :title="editingRule ? '编辑告警规则' : '新建告警规则'"
      :confirm-loading="ruleSaving"
      @ok="saveRule"
    >
      <a-form :model="ruleForm" layout="vertical">
        <a-form-item label="规则名称" required>
          <a-input v-model:value="ruleForm.name" maxlength="255" placeholder="如：磁盘空间告警" />
        </a-form-item>
        <a-form-item label="规则类型">
          <a-select v-model:value="ruleForm.rule_type">
            <a-select-option v-for="t in RULE_TYPES" :key="t.value" :value="t.value">{{ t.label }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="阈值（按规则类型解释；留空使用默认值）">
          <a-input v-model:value="ruleForm.threshold" maxlength="50" placeholder="如磁盘剩余比例 90 / 连续失败次数 2" />
        </a-form-item>
        <a-form-item label="冷却时间（秒）">
          <a-input-number v-model:value="ruleForm.cooldown_seconds" :min="0" :step="60" style="width: 180px" />
        </a-form-item>
        <a-form-item label="通知邮箱">
          <a-input v-model:value="ruleForm.notify_email" maxlength="255" placeholder="SMTP 配置在环境变量中设置" />
        </a-form-item>
        <a-form-item label="启用">
          <a-switch v-model:checked="ruleForm.enabled" checked-children="启用" un-checked-children="停用" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 发送测试邮件 -->
    <a-modal
      v-model:open="testEmailVisible"
      title="发送测试邮件"
      :confirm-loading="emailSending"
      @ok="sendTestEmail"
    >
      <a-form layout="vertical">
        <a-form-item label="收件人邮箱" required>
          <a-input v-model:value="testEmailAddr" placeholder="SMTP 未配置时将返回 503" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.operations-view {
  padding: 16px;
  color: var(--text-primary);
}
.ops-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.event-toolbar {
  margin-top: 16px;
}
.status-block {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 12px;
  background-color: var(--surface);
}
.status-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.status-title {
  font-weight: 600;
}
.status-loading {
  color: var(--text-secondary);
  padding: 8px 0;
}
.status-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
  border-top: 1px dashed var(--border);
}
.status-name {
  width: 120px;
  flex: 0 0 auto;
  color: var(--text-primary);
}
.status-detail {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text-secondary);
  overflow-wrap: break-word;
}
.preview-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
}
.preview-line {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.preview-files {
  color: var(--text-secondary);
  overflow-wrap: break-word;
}
.ops-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.change-text {
  color: var(--text-primary);
  overflow-wrap: break-word;
}
.detail-json {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  background: var(--surface-raised);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px;
  font-size: 12px;
  color: var(--text-primary);
}
.form-hint {
  color: var(--text-secondary);
  font-size: 12px;
  margin-top: 4px;
}
.muted {
  color: var(--text-secondary);
}
</style>
