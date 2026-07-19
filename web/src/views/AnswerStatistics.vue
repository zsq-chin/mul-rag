<script setup>
import { onMounted, ref, reactive, computed } from 'vue'
import { message } from 'ant-design-vue'
import { 
  MessageCircle, TrendingUp, HelpCircle, Activity, Search, 
  ChevronRight, Award, Filter, Clock, Hash
} from 'lucide-vue-next'
// [新增] 导入刚才定义的 API
import { getTopQuestions, getQuestionDiscussions, createDiscussion, createHelpRequest } from '@/apis/statistics_api'
// --- 状态定义 ---
const loading = ref(false)
const showHelpModal = ref(false)
const showDiscussionDrawer = ref(false)
const selectedQuestion = ref(null)
const searchText = ref('') 
const selectedCategory = ref('all') 

const helpForm = reactive({ title: '', description: '', email: '' })
const discussionComments = ref([])
const newComment = ref('')

// --- [修改] 数据源：不再使用写死的模拟数据，而是初始化为空 ---
const topQuestions = ref([])
const allQuestions = ref([])

// --- 模拟数据（后端无数据时使用）---
const mockData = [
  { id: 1, title: '水力压裂的最优泵入速率是多少？', count: 245, category: '技术', lastAsked: '2024-01-12', discussionCount: 8, helpCount: 0, description: '在不同地层条件下，如何确定水力压裂的最优泵入速率？' },
  { id: 2, title: '压裂液配方选择的关键因素', count: 189, category: '工程', lastAsked: '2024-01-11', discussionCount: 6, helpCount: 1, description: '如何根据地层特性选择合适的压裂液配方？' },
  { id: 3, title: '支撑剂粒度对压裂效果的影响', count: 156, category: '技术', lastAsked: '2024-01-10', discussionCount: 5, helpCount: 2, description: '不同支撑剂粒度如何影响压裂后的产能？' },
  { id: 4, title: '压裂污水处理与回收技术', count: 134, category: '环保', lastAsked: '2024-01-09', discussionCount: 4, helpCount: 1, description: '如何高效处理和回收压裂过程中产生的污水？' },
  { id: 5, title: '压裂诱导裂缝方向控制', count: 127, category: '技术', lastAsked: '2024-01-08', discussionCount: 3, helpCount: 0, description: '如何有效控制压裂过程中诱导裂缝的扩展方向？' },
  { id: 6, title: '多段塞式压裂设计方案', count: 98, category: '工程', lastAsked: '2024-01-07', discussionCount: 7, helpCount: 0, description: '多段塞式压裂的具体施工参数如何优化？' },
  { id: 7, title: '压裂参数优化与产能预测', count: 87, category: '技术', lastAsked: '2024-01-06', discussionCount: 6, helpCount: 0, description: '压裂参数与产能的关系模型' },
  { id: 8, title: '页岩气压裂工艺技术对比', count: 76, category: '工程', lastAsked: '2024-01-05', discussionCount: 5, helpCount: 0, description: '不同压裂工艺的对比分析' },
]

// --- [新增] 获取数据的方法 ---
const fetchTopQuestions = async () => {
  loading.value = true
  try {
    console.log('[DEBUG] 开始获取热门问题...')
    // 调用后端接口，获取所有问题（limit设为较大数值）
    const res = await getTopQuestions({ limit: 50 }) 
    console.log('[DEBUG] 后端响应:', res)
    
    if (res && res.data && res.data.status === 'success' && res.data.data && res.data.data.length > 0) {
      console.log('[DEBUG] 获取后端数据成功，数据条数:', res.data.data.length)
      const data = res.data.data
      // 前5个作为热门问题
      topQuestions.value = data.slice(0, 5)
      // 所有问题放入 allQuestions
      allQuestions.value = data
    } else {
      console.warn('[DEBUG] 后端无数据，使用模拟数据')
      // 后端无数据，使用模拟数据
      topQuestions.value = mockData.slice(0, 5)
      allQuestions.value = mockData
      message.info('使用演示数据（后端未连接）')
    }
  } catch (error) {
    console.error('[DEBUG] 获取热门问题失败:', error)
    // 捕获错误，使用模拟数据
    console.warn('[DEBUG] 使用备用模拟数据')
    topQuestions.value = mockData.slice(0, 5)
    allQuestions.value = mockData
    message.warning('无法连接后端，已加载演示数据')
  } finally {
    loading.value = false
  }
}

const mockDiscussions = {
  1: [
    { id: 1, author: '张三', avatar: '', time: '2024-01-12 14:30', content: '我们团队最近也遇到这个问题，可以考虑使用图分析的并行化方案。' },
  ]
}

// --- 计算属性 ---
const statCards = computed(() => [
  { label: '热门问题', value: topQuestions.value.length, icon: Activity, color: '#3b82f6', bg: 'rgba(59, 130, 246, 0.1)' },
  { label: '总提问数', value: allQuestions.value.reduce((sum, q) => sum + q.count, 0), icon: Search, color: '#10b981', bg: 'rgba(16, 185, 129, 0.1)' },
  { label: '讨论热度', value: allQuestions.value.reduce((sum, q) => sum + q.discussionCount || 0, 0), icon: MessageCircle, color: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.1)' },
  { label: '待响应求助', value: 12, icon: HelpCircle, color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.1)' },
])

const filteredAllQuestions = computed(() => {
  return allQuestions.value.filter(q => {
    const matchSearch = q.title.toLowerCase().includes(searchText.value.toLowerCase())
    const matchCategory = selectedCategory.value === 'all' || q.category === selectedCategory.value
    return matchSearch && matchCategory
  })
})

// --- 方法 ---
// 1. 打开讨论区
const openDiscussion = async (question) => {
  selectedQuestion.value = question
  showDiscussionDrawer.value = true
  // [新增] 加载真实评论
  try {
    const res = await getQuestionDiscussions(question.id)
    if (res.data && res.data.status === 'success') {
      discussionComments.value = res.data.data
    }
  } catch (error) {
    message.error('加载评论失败')
  }
}

// 2. 发送评论
const submitComment = async () => {
  if (!newComment.value.trim()) return
  
  try {
    // [新增] 调用后端接口
    const res = await createDiscussion(selectedQuestion.value.id, {
      content: newComment.value
    })
    
    if (res.data && res.data.status === 'success') {
      // 把新评论加到列表里
      discussionComments.value.push(res.data.data)
      newComment.value = ''
      message.success('评论发表成功')
      
      // 可选：刷新一下列表以更新评论数
      fetchTopQuestions() 
    }
  } catch (error) {
    message.error('评论发表失败')
  }
}

// 修复：确保 openHelpModal 能够被正确调用
const openHelpModal = (question) => {
  selectedQuestion.value = question
  showHelpModal.value = true
}

// 3. 提交求助
const submitHelpRequest = async () => {
  if (!helpForm.title || !helpForm.email) {
    message.warning('请补全求助信息')
    return
  }
  
  try {
    // [新增] 调用后端接口
    // 注意：后端需要 questionId，如果这是针对某个具体问题的求助，需要传入
    // 这里假设是通用求助，或者你需要先选一个问题。
    // 如果你的求助必须关联问题，请确保 selectedQuestion 有值。
    // 如果是通用求助，后端接口可能需要调整，或者传入一个默认ID。
    
    // 假设这里的逻辑是：针对列表中点击了“求助”的那个问题
    if (!selectedQuestion.value) {
       message.warning('请先选择一个相关问题')
       return
    }

    const res = await createHelpRequest({
      questionId: selectedQuestion.value.id,
      title: helpForm.title,
      description: helpForm.description,
      email: helpForm.email
    })

    if (res.data && res.data.status === 'success') {
      message.success('求助已提交，专家将通过邮件联系您')
      showHelpModal.value = false
      // 重置表单
      helpForm.title = ''
      helpForm.description = ''
      helpForm.email = ''
    }
  } catch (error) {
    message.error('提交失败: ' + error.message)
  }
}


// 4. 打开求助框 (需要记录当前是针对哪个问题)
const handleHelpClick = (question) => {
  selectedQuestion.value = question // [重要] 记录当前针对哪个问题求助
  helpForm.title = `关于"${question.title}"的咨询` // 自动填充标题
  showHelpModal.value = true
}


const getCategoryColor = (category) => {
  const map = { '技术': 'blue', '工程': 'cyan', '环保': 'green', '用户提问': 'purple' }
  return map[category] || 'gray'
}

// --- [修改] 生命周期：页面加载时触发请求 ---
onMounted(() => {
  fetchTopQuestions()
})

// --- 计算属性保持不变 ---
const filteredQuestions = computed(() => {
  return topQuestions.value.filter(q => {
    const matchSearch = q.title.toLowerCase().includes(searchText.value.toLowerCase()) || 
                        q.description.toLowerCase().includes(searchText.value.toLowerCase())
    const matchCategory = selectedCategory.value === 'all' || q.category === selectedCategory.value
    return matchSearch && matchCategory
  })
})

const stats = computed(() => ({
  totalQuestions: topQuestions.value.length,
  totalViews: topQuestions.value.reduce((acc, cur) => acc + (cur.count || 0), 0),
  totalDiscussions: topQuestions.value.reduce((acc, cur) => acc + (cur.discussionCount || 0), 0),
  solvedCount: topQuestions.value.reduce((acc, cur) => acc + (cur.helpCount || 0), 0)
}))

const getRankIcon = (index) => {
  if (index === 0) return 'text-yellow-500' // 金
  if (index === 1) return 'text-gray-400'   // 银
  if (index === 2) return 'text-orange-500' // 铜
  return 'text-blue-200'
}

</script>

<template>
  <div class="statistics-container">
    <div class="bg-blob blob-1"></div>
    <div class="bg-blob blob-2"></div>

    <div class="header-section">
      <div class="title-row">
        <div class="icon-box"><TrendingUp size="24" color="white" /></div>
        <div class="title-text">
          <h1>工程问答社区数据</h1>
          <p>实时监控问题趋势与专家互动情况</p>
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
              style="width: 100px" 
              class="category-select"
            >
              <a-select-option value="all">全部分类</a-select-option>
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

    <a-drawer
      v-model:open="showDiscussionDrawer"
      title="讨论详情"
      width="500"
      @close="selectedQuestion = null"
    >
       <template v-if="selectedQuestion">
         <h3>{{ selectedQuestion.title }}</h3>
         <div v-for="c in discussionComments" :key="c.id" style="padding: 10px; border-bottom: 1px solid #eee;">
            <b>{{ c.author }}:</b> {{ c.content }}
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
// 变量
@primary: #3b82f6;
@warning: #f59e0b; // 新增警告/求助色
@bg: #f8fafc;
@glass: rgba(255, 255, 255, 0.7);
@border: 1px solid rgba(255, 255, 255, 0.5);

.statistics-container {
  padding: 24px 32px;
  background: @bg;
  min-height: 100vh;
  position: relative;
  overflow: hidden;
  font-family: -apple-system, sans-serif;

  .bg-blob {
    position: absolute;
    border-radius: 50%;
    filter: blur(80px);
    z-index: 0;
    opacity: 0.5;
    &.blob-1 { top: -100px; right: -50px; width: 500px; height: 500px; background: rgba(59, 130, 246, 0.15); }
    &.blob-2 { bottom: 0; left: -100px; width: 400px; height: 400px; background: rgba(16, 185, 129, 0.1); }
  }

  .glass-panel {
    background: @glass;
    backdrop-filter: blur(12px);
    border: @border;
    border-radius: 16px;
    box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
  }

  // 1. 头部区域 (保持不变)
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
      h1 { margin: 0; font-size: 24px; font-weight: 700; color: #1e293b; }
      p { margin: 4px 0 0; color: #64748b; font-size: 14px; }
    }

    .stats-row {
      display: flex;
      gap: 16px;
      .mini-stat-card {
        padding: 12px 20px;
        display: flex; align-items: center; gap: 12px;
        transition: transform 0.2s;
        &:hover { transform: translateY(-2px); }
        .stat-info {
          display: flex; flex-direction: column;
          .val { font-weight: 700; font-size: 18px; line-height: 1.2; color: #0f172a; }
          .lbl { font-size: 12px; color: #64748b; }
        }
      }
    }
  }

  // 2. 主体 Grid 布局
  .main-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 32px;
    position: relative;
    z-index: 1;
    align-items: start;
  }

  // 左侧：热门榜单
  .left-column {
    .section-header {
      margin-bottom: 20px;
      h2 { 
        font-size: 18px; font-weight: 600; color: #1e293b; display: flex; align-items: center; gap: 8px;
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

        &:hover { transform: scale(1.01); background: rgba(255,255,255,0.9); }

        .rank-badge {
          width: 32px; height: 32px; border-radius: 8px; flex-shrink: 0;
          display: flex; align-items: center; justify-content: center;
          font-weight: 700; font-size: 14px; background: #f1f5f9; color: #64748b;
          &.rank-1 { background: linear-gradient(135deg, #fbbf24, #d97706); color: white; }
          &.rank-2 { background: linear-gradient(135deg, #94a3b8, #64748b); color: white; }
          &.rank-3 { background: linear-gradient(135deg, #d4a373, #a98467); color: white; }
        }

        .q-content {
          flex: 1; min-width: 0;
          .q-title { font-size: 15px; font-weight: 600; color: #333; margin: 0 0 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .q-meta {
            display: flex; gap: 12px; align-items: center; font-size: 12px; color: #94a3b8;
            .tiny-tag { margin: 0; padding: 0 6px; font-size: 11px; line-height: 18px; border:none; }
            .meta-icon { display: flex; align-items: center; gap: 4px; }
          }
        }

        // 按钮组样式
        .q-actions {
          display: flex; gap: 8px;
          .action-btn {
            border: none; background: transparent; cursor: pointer; padding: 6px; border-radius: 6px;
            display: flex; align-items: center; justify-content: center; transition: all 0.2s;
            color: #94a3b8;
            
            &:hover { background: #f1f5f9; }
            &.help-btn:hover { color: @warning; background: rgba(245, 158, 11, 0.1); }
            &.view-btn:hover { color: @primary; background: rgba(59, 130, 246, 0.1); }
          }
        }
      }
    }
  }

  // 右侧：所有问题面板
  .right-column {
    height: 600px;
    display: flex; flex-direction: column; padding: 0; overflow: hidden;

    .panel-header {
      padding: 20px 24px;
      border-bottom: 1px solid rgba(0,0,0,0.05);
      display: flex; flex-direction: column; gap: 16px;
      
      .header-left {
        display: flex; align-items: center; gap: 8px;
        h3 { margin: 0; font-size: 16px; font-weight: 600; }
        .count-badge { background: #e2e8f0; padding: 2px 8px; border-radius: 10px; font-size: 12px; color: #64748b; }
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
          background: rgba(255,255,255,0.6); 
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
            .item-title { font-size: 14px; color: #333; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
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
}

@keyframes slideIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (max-width: 1024px) {
  .header-section { flex-direction: column; align-items: flex-start; gap: 20px; }
  .main-grid { grid-template-columns: 1fr; }
  .right-column { height: 500px; }
}
</style>