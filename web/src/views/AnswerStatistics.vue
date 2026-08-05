<script setup>
import { onMounted, ref, reactive, computed, nextTick, onBeforeUnmount } from 'vue'
import { message } from 'ant-design-vue'
import * as echarts from 'echarts'
import {
  MessageCircle, TrendingUp, HelpCircle, Activity, Search,
  ChevronRight, Award, Users, MessagesSquare, Hash, BarChart3
} from 'lucide-vue-next'
import {
  getStatisticsOverview, getTopQuestions, getQuestionDiscussions,
  createDiscussion, createHelpRequest, syncQuestions
} from '@/apis/statistics_api'

// --- 状态定义 ---
const loading = ref(false)
const overview = ref(null) // 统计总览数据
const showHelpModal = ref(false)
const showDiscussionDrawer = ref(false)
const selectedQuestion = ref(null)
const searchText = ref('')
const selectedCategory = ref('all')

const helpForm = reactive({ title: '', description: '', email: '' })
const discussionComments = ref([])
const newComment = ref('')

// 社区板块数据源（由 sync-questions 从真实对话聚合而来）
const topQuestions = ref([])
const allQuestions = ref([])

// --- echarts 图表 ---
const trendChartRef = ref(null)
const distChartRef = ref(null)
const usersChartRef = ref(null)
let trendChart = null
let distChart = null
let usersChart = null

// --- 数据加载 ---
const loadAll = async () => {
  loading.value = true
  try {
    // 1. 先把真实热门问题同步进社区 questions 表
    try {
      await syncQuestions()
    } catch (e) {
      console.warn('[问答统计] 同步热门问题失败，将继续加载统计数据', e)
    }
    // 2. 加载社区问题
    const topRes = await getTopQuestions({ limit: 50 })
    if (topRes && topRes.status === 'success' && Array.isArray(topRes.data)) {
      allQuestions.value = topRes.data
      topQuestions.value = topRes.data.slice(0, 5)
    }
    // 3. 加载统计总览
    const ov = await getStatisticsOverview({ days: 14 })
    if (ov && ov.status === 'success' && ov.data) {
      overview.value = ov.data
    }
    await nextTick()
    renderCharts()
  } catch (error) {
    console.error('[问答统计] 加载失败:', error)
    message.error('统计数据加载失败: ' + (error.message || '未知错误'))
  } finally {
    loading.value = false
  }
}

// --- 图表渲染 ---
const renderCharts = () => {
  renderTrendChart()
  renderDistChart()
  renderUsersChart()
}

const renderTrendChart = () => {
  if (!overview.value || !trendChartRef.value) return
  const data = overview.value.daily_trend || []
  if (!trendChart) trendChart = echarts.init(trendChartRef.value)
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['提问', '回答', '会话'], bottom: 0, icon: 'roundRect', itemWidth: 12, itemHeight: 8 },
    grid: { left: 8, right: 16, top: 28, bottom: 44, containLabel: true },
    xAxis: { type: 'category', data: data.map(d => d.date.slice(5)), axisLine: { lineStyle: { color: 'var(--border)' } } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: 'var(--border)' } } },
    series: [
      { name: '提问', type: 'line', smooth: true, data: data.map(d => d.questions), itemStyle: { color: '#3b82f6' }, areaStyle: { opacity: 0.08 } },
      { name: '回答', type: 'line', smooth: true, data: data.map(d => d.answers), itemStyle: { color: '#10b981' } },
      { name: '会话', type: 'bar', barMaxWidth: 16, data: data.map(d => d.conversations), itemStyle: { color: 'rgba(139, 92, 246, 0.35)' } }
    ]
  })
}

const renderDistChart = () => {
  if (!overview.value || !distChartRef.value) return
  const dist = (overview.value.agent_distribution || []).slice(0, 8)
  if (!distChart) distChart = echarts.init(distChartRef.value)
  distChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', right: 8, top: 'middle', textStyle: { fontSize: 12 } },
    series: [{
      type: 'pie', radius: ['42%', '68%'], center: ['36%', '50%'],
      data: dist,
      label: { show: false },
      emphasis: { label: { show: true, fontWeight: 'bold', formatter: '{b}\n{c}' } }
    }]
  })
}

const renderUsersChart = () => {
  if (!overview.value || !usersChartRef.value) return
  const users = overview.value.top_users || []
  if (!usersChart) usersChart = echarts.init(usersChartRef.value)
  const names = users.map(u => u.username || `用户${u.user_id}`)
  usersChart.setOption({
    tooltip: { trigger: 'axis', formatter: '{b}: 提问 {c} 次' },
    grid: { left: 8, right: 16, top: 20, bottom: 28, containLabel: true },
    xAxis: { type: 'category', data: names, axisLabel: { interval: 0, width: 64, overflow: 'truncate' }, axisLine: { lineStyle: { color: 'var(--border)' } } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: 'var(--border)' } } },
    series: [{ type: 'bar', barMaxWidth: 28, data: users.map(u => u.questions), itemStyle: { color: '#8b5cf6', borderRadius: [4, 4, 0, 0] } }]
  })
}

const handleResize = () => {
  trendChart && trendChart.resize()
  distChart && distChart.resize()
  usersChart && usersChart.resize()
}

// --- 计算属性 ---
const statCards = computed(() => {
  const t = overview.value?.totals || {}
  return [
    { label: '总提问数', value: t.questions ?? 0, icon: Search, color: '#3b82f6', bg: 'rgba(59, 130, 246, 0.1)' },
    { label: '总回答数', value: t.answers ?? 0, icon: MessageCircle, color: '#10b981', bg: 'rgba(16, 185, 129, 0.1)' },
    { label: '会话记录', value: t.conversations ?? 0, icon: MessagesSquare, color: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.1)' },
    { label: '活跃用户', value: t.active_users ?? 0, icon: Users, color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.1)' },
    { label: '对话线程', value: t.threads ?? 0, icon: Hash, color: '#06b6d4', bg: 'rgba(6, 182, 212, 0.1)' }
  ]
})

const filteredAllQuestions = computed(() => {
  return allQuestions.value.filter(q => {
    const matchSearch = (q.title || '').toLowerCase().includes(searchText.value.toLowerCase())
    const matchCategory = selectedCategory.value === 'all' || (q.category || '') === selectedCategory.value
    return matchSearch && matchCategory
  })
})

const getCategoryColor = (category) => {
  const map = { '技术': 'blue', '工程': 'cyan', '环保': 'green', '用户提问': 'purple' }
  return map[category] || 'gray'
}

const getRankIcon = (index) => {
  if (index === 0) return 'text-yellow-500'
  if (index === 1) return 'text-gray-400'
  if (index === 2) return 'text-orange-500'
  return 'text-blue-200'
}

// --- 讨论区 ---
const openDiscussion = async (question) => {
  selectedQuestion.value = question
  showDiscussionDrawer.value = true
  try {
    const res = await getQuestionDiscussions(question.id)
    discussionComments.value = (res && res.status === 'success' && Array.isArray(res.data)) ? res.data : []
  } catch (error) {
    discussionComments.value = []
    message.error('加载评论失败')
  }
}

const submitComment = async () => {
  if (!newComment.value.trim()) return
  try {
    const res = await createDiscussion(selectedQuestion.value.id, { content: newComment.value })
    if (res && res.status === 'success') {
      discussionComments.value.push({
        id: res.data?.id,
        author: '当前用户',
        time: res.data?.time || '',
        content: newComment.value
      })
      newComment.value = ''
      message.success('评论发表成功')
      loadAll()
    }
  } catch (error) {
    message.error('评论发表失败')
  }
}

// --- 求助 ---
const handleHelpClick = (question) => {
  selectedQuestion.value = question
  helpForm.title = `关于"${question.title}"的咨询`
  showHelpModal.value = true
}

const openHelpModal = (question) => {
  handleHelpClick(question)
}

const submitHelpRequest = async () => {
  if (!helpForm.title || !helpForm.email) {
    message.warning('请补全求助信息')
    return
  }
  if (!selectedQuestion.value) {
    message.warning('请先选择一个相关问题')
    return
  }
  try {
    const res = await createHelpRequest({
      questionId: selectedQuestion.value.id,
      title: helpForm.title,
      description: helpForm.description,
      email: helpForm.email
    })
    if (res && res.status === 'success') {
      message.success('求助已提交，专家将通过邮件联系您')
      showHelpModal.value = false
      helpForm.title = ''
      helpForm.description = ''
      helpForm.email = ''
    }
  } catch (error) {
    message.error('提交失败: ' + (error.message || ''))
  }
}

// --- 生命周期 ---
onMounted(() => {
  loadAll()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  trendChart && trendChart.dispose()
  distChart && distChart.dispose()
  usersChart && usersChart.dispose()
  trendChart = distChart = usersChart = null
})
</script>

<template>
  <div class="statistics-container">
    <a-spin :spinning="loading" wrapper-class-name="statistics-spin">
      <div class="header-section">
        <div class="title-row">
          <div class="icon-box"><TrendingUp size="24" color="white" /></div>
          <div class="title-text">
            <h1>问答统计</h1>
            <p>基于真实问答数据的实时统计与社区互动</p>
          </div>
        </div>

        <div class="stats-row">
          <div v-for="(card, index) in statCards" :key="index" class="mini-stat-card glass-panel">
            <component :is="card.icon" size="20" :style="{ color: card.color }" />
            <div class="stat-info">
              <span class="val">{{ card.value }}</span>
              <span class="lbl">{{ card.label }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 真实统计图表区 -->
      <div class="charts-grid">
        <div class="chart-card glass-panel chart-wide">
          <div class="panel-header">
            <div class="header-left">
              <BarChart3 size="16" />
              <h3>每日问答趋势</h3>
            </div>
            <span class="sub-hint">近 14 天提问 / 回答 / 会话</span>
          </div>
          <div ref="trendChartRef" class="chart-body" style="height: 280px;"></div>
        </div>

        <div class="chart-card glass-panel">
          <div class="panel-header">
            <div class="header-left">
              <Activity size="16" />
              <h3>按智能体分布</h3>
            </div>
          </div>
          <div ref="distChartRef" class="chart-body" style="height: 280px;"></div>
        </div>

        <div class="chart-card glass-panel">
          <div class="panel-header">
            <div class="header-left">
              <Award size="16" />
              <h3>热门问题 TOP10</h3>
            </div>
            <span class="sub-hint">按提问次数</span>
          </div>
          <div class="hot-list chart-body">
            <div v-if="!overview || !overview.hot_questions || overview.hot_questions.length === 0" class="empty-state">
              <Search size="32" color="#e2e8f0" />
              <p>暂无热门问题</p>
            </div>
            <div v-for="(item, index) in (overview?.hot_questions || []).slice(0, 10)" :key="index" class="hot-item">
              <span class="hot-rank" :class="getRankIcon(index)">{{ index + 1 }}</span>
              <span class="hot-title">{{ item.question }}</span>
              <span class="hot-count">{{ item.count }} 次</span>
            </div>
          </div>
        </div>

        <div class="chart-card glass-panel">
          <div class="panel-header">
            <div class="header-left">
              <Users size="16" />
              <h3>活跃用户排行</h3>
            </div>
            <span class="sub-hint">按提问次数</span>
          </div>
          <div ref="usersChartRef" class="chart-body" style="height: 280px;"></div>
        </div>
      </div>

      <!-- 社区互动区 -->
      <div class="community-section">
        <div class="main-grid">
          <div class="left-column">
            <div class="section-header">
              <h2>🔥 热门提问榜 <span class="badge">Top 5</span></h2>
            </div>

            <div class="questions-list">
              <div
                v-for="(question, index) in topQuestions"
                :key="question.id"
                class="question-card glass-panel"
                :style="{ '--delay': index * 0.1 + 's' }"
              >
                <div class="rank-badge" :class="'rank-' + (index + 1)">
                  <Award v-if="index < 3" size="16" />
                  <span v-else>{{ index + 1 }}</span>
                </div>

                <div class="q-content">
                  <h3 class="q-title">{{ question.title }}</h3>
                  <div class="q-meta">
                    <a-tag :color="getCategoryColor(question.category)" class="tiny-tag">{{ question.category }}</a-tag>
                    <span class="meta-icon"><TrendingUp size="12" /> {{ question.count }}</span>
                    <span class="meta-icon"><MessageCircle size="12" /> {{ question.discussionCount }}</span>
                  </div>
                </div>

                <div class="q-actions">
                  <a-tooltip title="寻求专家帮助">
                    <button class="action-btn help-btn" @click.stop="openHelpModal(question)">
                      <HelpCircle size="16" />
                    </button>
                  </a-tooltip>
                  <a-tooltip title="查看讨论">
                    <button class="action-btn view-btn" @click.stop="openDiscussion(question)">
                      <ChevronRight size="16" />
                    </button>
                  </a-tooltip>
                </div>
              </div>
              <div v-if="topQuestions.length === 0" class="empty-state">
                <Search size="40" color="#e2e8f0" />
                <p>暂无热门问题，去问答页面提问吧</p>
              </div>
            </div>
          </div>

          <div class="right-column glass-panel">
            <div class="panel-header">
              <div class="header-left">
                <h3>📚 问题全库</h3>
                <span class="count-badge">{{ filteredAllQuestions.length }}</span>
              </div>
              <div class="header-tools">
                <a-input
                  v-model:value="searchText"
                  placeholder="搜索..."
                  class="search-input"
                >
                  <template #prefix><Search size="14" color="#94a3b8"/></template>
                </a-input>
                <a-select
                  v-model:value="selectedCategory"
                  style="width: 110px"
                  class="category-select"
                >
                  <a-select-option value="all">全部分类</a-select-option>
                  <a-select-option value="用户提问">用户提问</a-select-option>
                  <a-select-option value="技术">技术</a-select-option>
                  <a-select-option value="工程">工程</a-select-option>
                  <a-select-option value="环保">环保</a-select-option>
                </a-select>
              </div>
            </div>

            <div class="scrollable-list">
              <div v-if="filteredAllQuestions.length === 0" class="empty-state">
                <Search size="40" color="#e2e8f0" />
                <p>未找到相关问题</p>
              </div>

              <div
                v-for="item in filteredAllQuestions"
                :key="item.id"
                class="list-item"
                @click="openDiscussion(item)"
              >
                <div class="item-main">
                  <div class="item-title-row">
                    <span class="category-dot" :class="getCategoryColor(item.category)"></span>
                    <span class="item-title">{{ item.title }}</span>
                  </div>
                  <div class="item-desc" v-if="item.description">{{ item.description.substring(0, 30) }}...</div>
                </div>

                <div class="item-meta">
                  <span class="date">{{ item.lastAsked }}</span>
                  <div class="stats-row-small">
                    <a-tooltip title="专家求助">
                      <span class="icon-action help" @click.stop="handleHelpClick(item)">
                        <HelpCircle :size="13" />
                        <span v-if="item.helpCount" style="margin-left: 4px; font-size: 12px;">
                          {{ item.helpCount }}
                        </span>
                      </span>
                    </a-tooltip>
                    <span class="icon-info">
                      <MessageCircle size="13"/> {{ item.discussionCount }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </a-spin>

    <a-drawer
      v-model:open="showDiscussionDrawer"
      title="讨论详情"
      width="500"
      @close="selectedQuestion = null"
    >
      <template v-if="selectedQuestion">
        <h3>{{ selectedQuestion.title }}</h3>
        <div v-if="discussionComments.length === 0" class="drawer-empty">
          <p>暂无评论，来抢沙发~</p>
        </div>
        <div v-for="c in discussionComments" :key="c.id" class="discussion-item">
          <div class="discussion-head">
            <b>{{ c.author }}</b>
            <span>{{ c.time }}</span>
          </div>
          <div>{{ c.content }}</div>
        </div>
        <a-textarea v-model:value="newComment" :rows="4" style="margin-top:20px" placeholder="输入评论..." />
        <a-button type="primary" style="margin-top:10px" @click="submitComment">发送</a-button>
      </template>
    </a-drawer>

    <a-modal
      v-model:open="showHelpModal"
      title="专家求助通道"
      @ok="submitHelpRequest"
      ok-text="确认发送"
    >
      <template v-if="selectedQuestion">
        <p><strong>关于问题:</strong> {{ selectedQuestion.title }}</p>
        <a-form layout="vertical" style="margin-top: 15px">
          <a-form-item label="问题标题">
            <a-input v-model:value="helpForm.title" :placeholder="selectedQuestion.title" />
          </a-form-item>
          <a-form-item label="详细描述">
            <a-textarea v-model:value="helpForm.description" placeholder="请详细描述..." :rows="4" />
          </a-form-item>
          <a-form-item label="您的邮箱">
            <a-input v-model:value="helpForm.email" />
          </a-form-item>
        </a-form>
      </template>
    </a-modal>
  </div>
</template>

<style lang="less" scoped>
@primary: #3b82f6;
@warning: #f59e0b;
@bg: var(--app-bg);
@glass: var(--surface-raised);
@border: 1px solid var(--border);

.statistics-container {
  padding: 24px 32px;
  background: @bg;
  min-height: 100vh;
  position: relative;
  overflow: hidden;
  font-family: -apple-system, sans-serif;

  .glass-panel {
    background: @glass;
    backdrop-filter: blur(12px);
    border: @border;
    border-radius: 8px;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
  }

  // 1. 头部区域
  .header-section {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    margin-bottom: 32px;
    position: relative;
    z-index: 1;

    .title-row {
      display: flex;
      align-items: center;
      gap: 16px;
      .icon-box {
        width: 48px; height: 48px;
        background: linear-gradient(135deg, @primary, darken(@primary, 15%));
        border-radius: 12px;
        display: flex; align-items: center; justify-content: center;
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
      }
      h1 { margin: 0; font-size: 24px; font-weight: 700; color: var(--text-primary); }
      p { margin: 4px 0 0; color: var(--text-secondary); font-size: 14px; }
    }

    .stats-row {
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      justify-content: flex-end;
      .mini-stat-card {
        padding: 12px 20px;
        display: flex; align-items: center; gap: 12px;
        transition: transform 0.2s;
        &:hover { transform: translateY(-2px); }
        .stat-info {
          display: flex; flex-direction: column;
          .val { font-weight: 700; font-size: 18px; line-height: 1.2; color: var(--text-primary); }
          .lbl { font-size: 12px; color: var(--text-secondary); }
        }
      }
    }
  }

  // 2. 图表区
  .charts-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 20px;
    margin-bottom: 32px;
    position: relative;
    z-index: 1;

    .chart-wide {
      grid-column: span 1;
    }

    @media (min-width: 1200px) {
      grid-template-columns: 1fr 1fr;
    }

    .chart-card {
      padding: 18px 20px;
      overflow: hidden;

      .panel-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 12px;

        .header-left {
          display: flex; align-items: center; gap: 8px;
          color: var(--text-primary);
          h3 { margin: 0; font-size: 15px; font-weight: 600; }
        }
        .sub-hint { font-size: 12px; color: var(--text-secondary); }
      }

      .chart-body { width: 100%; }

      .hot-list {
        display: flex;
        flex-direction: column;
        gap: 4px;

        .hot-item {
          display: flex; align-items: center; gap: 10px;
          padding: 8px 10px;
          border-radius: 6px;
          transition: background 0.2s;
          &:hover { background: var(--hover); }

          .hot-rank {
            width: 22px; height: 22px; flex-shrink: 0;
            display: inline-flex; align-items: center; justify-content: center;
            border-radius: 6px;
            font-size: 12px; font-weight: 700;
            color: var(--text-secondary);
            background: var(--hover);
            &.text-yellow-500 { background: linear-gradient(135deg, #fbbf24, #d97706); color: #fff; }
            &.text-gray-400 { background: linear-gradient(135deg, #94a3b8, #64748b); color: #fff; }
            &.text-orange-500 { background: linear-gradient(135deg, #d4a373, #a98467); color: #fff; }
          }

          .hot-title {
            flex: 1; min-width: 0;
            font-size: 13px; color: var(--text-primary);
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
          }

          .hot-count { font-size: 12px; color: var(--text-secondary); flex-shrink: 0; }
        }
      }
    }
  }

  // 3. 社区区
  .community-section {
    position: relative;
    z-index: 1;
  }

  .main-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 32px;
    align-items: start;
  }

  .left-column {
    .section-header {
      margin-bottom: 20px;
      h2 {
        font-size: 18px; font-weight: 600; color: var(--text-primary); display: flex; align-items: center; gap: 8px;
        .badge { font-size: 12px; background: #fee2e2; color: #ef4444; padding: 2px 8px; border-radius: 4px; }
      }
    }

    .questions-list {
      display: flex; flex-direction: column; gap: 16px;

      .question-card {
        display: flex; align-items: center; gap: 16px; padding: 16px 20px;
        transition: all 0.3s;
        animation: slideIn 0.5s ease backwards;
        animation-delay: var(--delay);

        &:hover { transform: scale(1.01); background: var(--hover); }

        .rank-badge {
          width: 32px; height: 32px; border-radius: 8px; flex-shrink: 0;
          display: flex; align-items: center; justify-content: center;
          font-weight: 700; font-size: 14px; background: var(--hover); color: var(--text-secondary);
          &.rank-1 { background: linear-gradient(135deg, #fbbf24, #d97706); color: white; }
          &.rank-2 { background: linear-gradient(135deg, #94a3b8, #64748b); color: white; }
          &.rank-3 { background: linear-gradient(135deg, #d4a373, #a98467); color: white; }
        }

        .q-content {
          flex: 1; min-width: 0;
          .q-title { font-size: 15px; font-weight: 600; color: var(--text-primary); margin: 0 0 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .q-meta {
            display: flex; gap: 12px; align-items: center; font-size: 12px; color: #94a3b8;
            .tiny-tag { margin: 0; padding: 0 6px; font-size: 11px; line-height: 18px; border: none; }
            .meta-icon { display: flex; align-items: center; gap: 4px; }
          }
        }

        .q-actions {
          display: flex; gap: 8px;
          .action-btn {
            border: none; background: transparent; cursor: pointer; padding: 6px; border-radius: 6px;
            display: flex; align-items: center; justify-content: center; transition: all 0.2s;
            color: #94a3b8;

            &:hover { background: var(--hover); }
            &.help-btn:hover { color: @warning; background: rgba(245, 158, 11, 0.1); }
            &.view-btn:hover { color: @primary; background: rgba(59, 130, 246, 0.1); }
          }
        }
      }
    }
  }

  .right-column {
    height: 600px;
    display: flex; flex-direction: column; padding: 0; overflow: hidden;

    .panel-header {
      padding: 20px 24px;
      border-bottom: 1px solid var(--border);
      display: flex; flex-direction: column; gap: 16px;

      .header-left {
        display: flex; align-items: center; gap: 8px;
        h3 { margin: 0; font-size: 16px; font-weight: 600; }
        .count-badge { background: var(--hover); padding: 2px 8px; border-radius: 10px; font-size: 12px; color: var(--text-secondary); }
      }
      .header-tools {
        display: flex; gap: 12px;
        .search-input { flex: 1; }
        .category-select { width: 110px; }
      }
    }

    .scrollable-list {
      flex: 1; overflow-y: auto; padding: 12px;

      .empty-state {
        text-align: center; padding: 40px; color: #cbd5e1;
        p { margin-top: 10px; font-size: 13px; }
      }

      .list-item {
        padding: 12px 16px; border-radius: 8px; margin-bottom: 4px; cursor: pointer;
        display: flex; justify-content: space-between; align-items: center;
        transition: background 0.2s;

        &:hover {
          background: var(--hover);
          .stats-row-small .help { opacity: 1; transform: translateX(0); }
        }

        .item-main {
          flex: 1; min-width: 0; padding-right: 16px;
          .item-title-row {
            display: flex; align-items: center; gap: 8px; margin-bottom: 4px;
            .category-dot {
              width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; flex-shrink: 0;
              &.blue { background: #3b82f6; } &.cyan { background: #06b6d4; } &.green { background: #10b981; }
            }
            .item-title { font-size: 14px; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          }
          .item-desc { font-size: 12px; color: #94a3b8; }
        }

        .item-meta {
          text-align: right; flex-shrink: 0;
          .date { font-size: 12px; color: #94a3b8; display: block; margin-bottom: 2px; }

          .stats-row-small {
            display: flex; justify-content: flex-end; align-items: center; gap: 12px; font-size: 12px; color: #64748b;

            .icon-action.help {
              color: @warning; opacity: 0; transform: translateX(5px); transition: all 0.2s;
              &:hover { transform: scale(1.2); }
            }
            .icon-info { display: flex; align-items: center; gap: 4px; }
          }
        }
      }
    }
  }

  // 讨论抽屉
  .discussion-item {
    padding: 12px 0;
    border-bottom: 1px solid #eee;
    .discussion-head {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 4px;
      span { font-size: 12px; color: #94a3b8; }
    }
  }
  .drawer-empty { text-align: center; color: #cbd5e1; padding: 24px 0; }

  .empty-state {
    text-align: center; padding: 24px; color: #cbd5e1;
    p { margin-top: 8px; font-size: 13px; }
  }
}

@keyframes slideIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (max-width: 1024px) {
  .header-section { flex-direction: column; align-items: flex-start; gap: 20px; }
  .main-grid { grid-template-columns: 1fr; }
  .charts-grid { grid-template-columns: 1fr; }
  .right-column { height: 500px; }
}
</style>
