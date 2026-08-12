<template>
  <div class="refs-sidebar-container" :class="{ 'visible': visible, 'pinned': isPinned }">
    <div class="overlay" v-if="visible && !isPinned" @click="$emit('update:visible', false)"></div>
    <div class="refs-sidebar">
      <div class="sidebar-header">
        <h3>参考信息</h3>
        <div class="sidebar-controls">
          <a-button type="text" @click="togglePin" class="pin-button">
            <PushpinOutlined :class="{ 'pinned': isPinned }" />
          </a-button>
          <a-button type="text" @click="$emit('update:visible', false)">
            <CloseOutlined />
          </a-button>
        </div>
      </div>
      <a-tabs v-model:activeKey="activeTab" class="refs-tabs">
        <!-- 关系图 -->
        <a-tab-pane v-if="graphRetrievalEnabled" key="graph" tab="关系图" :disabled="!hasGraphData">
          <div v-if="hasGraphData" class="graph-container">
            <GraphContainer :graphData="latestRefs.graph_base.results" ref="graphContainerRef" />
          </div>
          <div v-else class="empty-data">
            <p>当前对话没有关系图数据</p>
          </div>
        </a-tab-pane>

        <!-- 网页搜索 -->
        <a-tab-pane key="webSearch" tab="网页搜索" :disabled="!hasWebSearchData">
          <div v-if="hasWebSearchData" class="web-results-list">
            <div v-for="result in latestRefs.web_search.results" :key="result.url" class="web-result-card">
              <div class="card-header">
                <h3 class="result-title">
                  <a :href="result.url" target="_blank">{{ result.title }}</a>
                </h3>
              </div>
              <div class="card-body">
                <div class="result-text">{{ result.content }}</div>
              </div>
              <div class="card-footer">
                <div class="score-info">
                  <span>相关度：{{ getPercent(result.score) }}%</span>
                  <span>来源：{{ getDomain(result.url) }}</span>
                </div>
              </div>
            </div>
          </div>
          <div v-else class="empty-data">
            <p>当前对话没有网页搜索数据</p>
          </div>
        </a-tab-pane>

        <!-- 知识库 -->
        <a-tab-pane v-if="knowledgeRetrievalEnabled" key="knowledgeBase" tab="知识库" :disabled="!hasKnowledgeBaseData">
          <div v-if="hasKnowledgeBaseData">
            <div class="file-list">
              <a-collapse v-model:activeKey="activeFiles">
                <a-collapse-panel
                  v-for="(results, filename) in groupedKnowledgeResults"
                  :key="filename"
                  :header="filename"
                >
                  <!-- <div class="fileinfo" v-if="results.length > 0">
                    <p><FileOutlined /> {{ results[0].file.type }}</p>
                    <p><ClockCircleOutlined /> {{ formatDate(results[0].file.created_at) }}</p>
                  </div> -->
                  <div class="results-list">
                    <div v-for="res in results" :key="res.id" class="result-item">
                      <div class="result-meta">
                        <div class="score-info">
                          <span>
                            <strong>相似度：</strong>
                            <a-progress :percent="getPercent(res.distance)"/>
                          </span>
                          <span v-if="res.rerank_score">
                            <strong>重排序：</strong>
                            <a-progress :percent="getPercent(res.rerank_score)"/>
                          </span>
                        </div>
                        <div class="result-id">ID: #{{ res.id }}</div>
                      </div>
                      <RichReferenceContent class="result-text" :content="res.entity.text" />
                    </div>
                  </div>
                </a-collapse-panel>
              </a-collapse>
            </div>
          </div>
          <div v-else class="empty-data">
            <p>当前对话没有知识库查询数据</p>
          </div>
        </a-tab-pane>

        <a-tab-pane v-if="knowledgeRetrievalEnabled" key="multimodalKnowledgeBase" tab="多模态知识库" :disabled="!hasMultimodalKnowledgeBaseData">
          <div v-if="hasMultimodalKnowledgeBaseData">
            <div class="multimodal-kb-summary">
              <div>
                <strong>使用知识库：</strong>
                <span>{{ multimodalKbInfo.kbName || '未指定' }}</span>
              </div>
              <div v-if="multimodalKbInfo.status">
                <strong>检索状态：</strong>
                <span
                  :class="{
                    'status-error': ['error', 'budget_reached', 'deadline_reached', 'user_cancelled'].includes(multimodalKbInfo.status),
                  }"
                >
                  {{ multimodalKbInfo.statusText }}
                </span>
              </div>
              <p v-if="multimodalKbInfo.message">{{ multimodalKbInfo.message }}</p>
            </div>

            <div v-if="multimodalResultCount > 0" class="file-list">
              <a-collapse v-model:activeKey="activeMultimodalFiles">
                <a-collapse-panel
                  v-for="(results, filename) in groupedMultimodalResults"
                  :key="filename"
                  :header="filename"
                >
                  <div class="results-list">
                    <div v-for="res in results" :key="`${res.fileId || filename}-${res.rank || res.id}`" class="result-item">
                      <div class="result-meta">
                        <div class="score-info">
                          <span v-if="typeof res.score === 'number'">
                            <strong>相似度：</strong>
                            <a-progress :percent="getPercent(res.score)"/>
                          </span>
                          <span v-if="res.page">
                            <strong>页码：</strong>{{ res.page }}
                          </span>
                        </div>
                        <div class="result-id">ID: #{{ res.id || res.rank }}</div>
                      </div>
                      <RichReferenceContent class="result-text" :content="res.text" :images="res.images" />
                    </div>
                  </div>
                </a-collapse-panel>
              </a-collapse>
            </div>
            <div v-else class="empty-data">
              <p>{{ multimodalKbInfo.message || '当前多模态知识库没有检索到相关结果' }}</p>
            </div>
          </div>
          <div v-else class="empty-data">
            <p>当前对话没有多模态知识库检索数据</p>
          </div>
        </a-tab-pane>

        <!-- 多轮检索过程 -->
        <a-tab-pane v-if="hasMultiRoundData" key="multiRound" tab="多轮检索">
          <div class="multi-round-list">
            <div v-for="round in latestRefs.multi_round.rounds" :key="round.round" class="multi-round-round">
              <div class="round-header">
                <span class="round-badge">第 {{ round.round }} 轮</span>
                <span class="round-recall">命中 {{ round.recall }} 条</span>
                <span class="round-verdict" :class="round.has_value ? 'verdict-ok' : 'verdict-no'">
                  {{ round.has_value ? '✓ 有价值' : '✗ 无价值' }}
                </span>
              </div>
              <div class="round-queries">
                <span v-for="q in round.queries" :key="q" class="query-chip">{{ q }}</span>
              </div>
              <div v-if="round.reason" class="round-reason">{{ round.reason }}</div>
              <div v-if="round.next_keywords && round.next_keywords.length" class="round-keywords">
                <strong>下一轮建议检索：</strong>{{ round.next_keywords.join('、') }}
              </div>
            </div>
            <div class="multi-round-final">
              共 {{ latestRefs.multi_round.total_rounds }} 轮 / {{ latestRefs.multi_round.sub_queries.length }} 个查询
              <template v-if="latestRefs.multi_round.assessment && latestRefs.multi_round.assessment.has_value">
                — 模型确认检索到足够有价值的内容
              </template>
              <template v-else>
                — 模型未确认检索到足够有价值的内容
              </template>
            </div>
          </div>
        </a-tab-pane>
      </a-tabs>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, reactive, watch, nextTick } from 'vue'
import {
  FileOutlined,
  ClockCircleOutlined,
  CloseOutlined,
  PushpinOutlined
} from '@ant-design/icons-vue'
import GraphContainer from './GraphContainer.vue'
import RichReferenceContent from './RichReferenceContent.vue'
import { useUserStore } from '@/stores/user'
import { canUseGraph, canUseKnowledgeRetrieval } from '@/utils/access.mjs'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  latestRefs: {
    type: Object,
    default: () => ({})
  }
})

const emit = defineEmits(['update:visible', 'pin-change'])
const userStore = useUserStore()
const graphRetrievalEnabled = computed(() => canUseGraph(userStore.userRole))
const knowledgeRetrievalEnabled = computed(() => canUseKnowledgeRetrieval(userStore.userRole))
// 标签页相关
const activeTab = ref(graphRetrievalEnabled.value ? 'graph' : 'webSearch')
const activeFiles = ref([])
const activeMultimodalFiles = ref([])

// 是否固定侧边栏
const isPinned = ref(false)

// 切换固定状态
const togglePin = () => {
  isPinned.value = !isPinned.value
  emit('pin-change', isPinned.value)
}

// 计算属性：是否有各类数据
const hasGraphData = computed(() => {
  try {
    return !!(props.latestRefs &&
      props.latestRefs.graph_base &&
      props.latestRefs.graph_base.results &&
      props.latestRefs.graph_base.results.nodes &&
      Array.isArray(props.latestRefs.graph_base.results.nodes) &&
      props.latestRefs.graph_base.results.nodes.length > 0);
  } catch (e) {
    console.error('Error checking graph data:', e);
    return false;
  }
})

const hasWebSearchData = computed(() => {
  try {
    // 更加容忍数据结构的不完整
    return !!(props.latestRefs &&
      props.latestRefs.web_search &&
      Array.isArray(props.latestRefs.web_search.results) &&
      props.latestRefs.web_search.results.length > 0);
  } catch (e) {
    console.error('Error checking web search data:', e);
    return false;
  }
})

const hasKnowledgeBaseData = computed(() => {
  try {
    return !!(props.latestRefs &&
      props.latestRefs.knowledge_base &&
      props.latestRefs.knowledge_base.results &&
      Array.isArray(props.latestRefs.knowledge_base.results) &&
      props.latestRefs.knowledge_base.results.length > 0);
  } catch (e) {
    console.error('Error checking knowledge base data:', e);
    return false;
  }
})

const hasMultiRoundData = computed(() => {
  try {
    const mr = props.latestRefs?.multi_round
    return !!(mr && Array.isArray(mr.rounds) && mr.rounds.length > 0)
  } catch (e) {
    return false
  }
})

const hasMultimodalKnowledgeBaseData = computed(() => {
  try {
    const refs = props.latestRefs?.multimodal_knowledge_base
    return !!(
      props.latestRefs?.meta?.use_multimodal_kb ||
      refs?.results?.length > 0 ||
      refs?.message ||
      refs?.kb_id
    );
  } catch (e) {
    console.error('Error checking multimodal knowledge base data:', e);
    return false;
  }
})

const multimodalResultCount = computed(() => {
  const results = props.latestRefs?.multimodal_knowledge_base?.results
  return Array.isArray(results) ? results.length : 0
})

const multimodalKbInfo = computed(() => {
  const refs = props.latestRefs?.multimodal_knowledge_base || {}
  const status = refs.status || (refs.message ? 'error' : '')
  // C2.5：远端失败/检索为空/用户取消/达到预算必须呈现为不同状态，不能都伪装成空结果
  const statusTextMap = {
    ok: '检索成功',
    empty: '未检索到结果',
    error: '检索失败',
    no_kb_selected: '未选择多模态知识库',
    budget_reached: '已达检索预算',
    deadline_reached: '检索超时',
    user_cancelled: '检索已取消',
  }

  return {
    kbName: refs.kb_name || props.latestRefs?.meta?.multimodal_kb_name || refs.kb_id || '',
    status,
    statusText: statusTextMap[status] || status,
    message: refs.message || '',
  }
})

// 知识库结果按文件分组
const groupedKnowledgeResults = computed(() => {
  if (!hasKnowledgeBaseData.value) return {}

  return props.latestRefs.knowledge_base.results
    .filter(result => result.file && result.file.filename)
    .reduce((acc, result) => {
      const { filename } = result.file
      if (!acc[filename]) {
        acc[filename] = []
      }
      acc[filename].push(result)
      return acc
    }, {})
})

const groupedMultimodalResults = computed(() => {
  const results = props.latestRefs?.multimodal_knowledge_base?.results
  if (!Array.isArray(results) || results.length === 0) return {}

  return results
    .reduce((acc, result) => {
      const filename = result.fileName || result.fileId || '未知来源'
      if (!acc[filename]) {
        acc[filename] = []
      }
      acc[filename].push(result)
      return acc
    }, {})
})

// 自动选择有数据的第一个标签页
watch(() => props.visible, (newValue) => {
  console.log('RefsSidebar visible changed to', newValue, 'activeTab is', activeTab.value);

  if (newValue) {
    console.log('Checking which tabs are available');
    // 只有在activeTab无效的情况下才自动选择标签页
    const currentTabValid =
      (graphRetrievalEnabled.value && activeTab.value === 'graph' && hasGraphData.value) ||
      (activeTab.value === 'webSearch' && hasWebSearchData.value) ||
      (knowledgeRetrievalEnabled.value && activeTab.value === 'knowledgeBase' && hasKnowledgeBaseData.value) ||
      (knowledgeRetrievalEnabled.value && activeTab.value === 'multimodalKnowledgeBase' && hasMultimodalKnowledgeBaseData.value) ||
      (activeTab.value === 'multiRound' && hasMultiRoundData.value);

    if (!currentTabValid) {
      console.log('Current tab is invalid, finding first available tab');
      // 当前标签无效，需要寻找一个有效的标签
      if (graphRetrievalEnabled.value && hasGraphData.value) {
        console.log('Selected graph tab');
        activeTab.value = 'graph';
      } else if (hasWebSearchData.value) {
        console.log('Selected webSearch tab');
        activeTab.value = 'webSearch';
      } else if (knowledgeRetrievalEnabled.value && hasKnowledgeBaseData.value) {
        console.log('Selected knowledgeBase tab');
        activeTab.value = 'knowledgeBase';
        // 打开第一个文件
        if (Object.keys(groupedKnowledgeResults.value).length > 0) {
          activeFiles.value = [Object.keys(groupedKnowledgeResults.value)[0]];
        }
      } else if (knowledgeRetrievalEnabled.value && hasMultimodalKnowledgeBaseData.value) {
        console.log('Selected multimodalKnowledgeBase tab');
        activeTab.value = 'multimodalKnowledgeBase';
        if (Object.keys(groupedMultimodalResults.value).length > 0) {
          activeMultimodalFiles.value = [Object.keys(groupedMultimodalResults.value)[0]];
        }
      } else if (hasMultiRoundData.value) {
        console.log('Selected multiRound tab');
        activeTab.value = 'multiRound';
      } else {
        console.log('No valid tabs available');
      }
    } else {
      console.log('Current tab is valid, keeping it:', activeTab.value);
    }
  }
});

// 百分比计算函数
const getPercent = (value) => {
  const numberValue = Number(value)
  if (!Number.isFinite(numberValue)) return 0
  return parseFloat((Math.max(0, Math.min(1, numberValue)) * 100).toFixed(2))
}

const getDomain = (url) => {
  return url.split('/')[2]
}

// 日期格式化函数
const formatDate = (timestamp) => {
  return new Date(timestamp * 1000).toLocaleString()
}

// 手动设置活动标签页
const setActiveTab = (tab) => {
  console.log('RefsSidebar setActiveTab called with tab:', tab);
  console.log('Current tabs available:', {
    graph: hasGraphData.value,
    webSearch: hasWebSearchData.value,
    knowledgeBase: hasKnowledgeBaseData.value,
    multimodalKnowledgeBase: hasMultimodalKnowledgeBaseData.value
  });
  console.log('Full latestRefs structure:', JSON.stringify(props.latestRefs));

  // 如果要设置的标签是有效的，直接设置
  if (!graphRetrievalEnabled.value && tab === 'graph') {
    activeTab.value = 'webSearch'
    return
  }

  if ((tab === 'graph' && hasGraphData.value) ||
      (tab === 'webSearch' && hasWebSearchData.value) ||
      (tab === 'knowledgeBase' && hasKnowledgeBaseData.value) ||
      (tab === 'multimodalKnowledgeBase' && hasMultimodalKnowledgeBaseData.value) ||
      (tab === 'multiRound' && hasMultiRoundData.value)) {
    console.log('Setting activeTab to:', tab);
    activeTab.value = tab;
    if (tab === 'multimodalKnowledgeBase' && Object.keys(groupedMultimodalResults.value).length > 0) {
      activeMultimodalFiles.value = [Object.keys(groupedMultimodalResults.value)[0]];
    }
  } else {
    console.warn(`Cannot set tab to ${tab}, tab is disabled or invalid`);

    // 如果特定标签数据不可用，但数据存在，尝试强制启用相应标签
    if (tab === 'webSearch' && props.latestRefs.web_search) {
      console.log('Forcing webSearch tab even though hasWebSearchData is false');
      activeTab.value = 'webSearch';
    } else if (tab === 'knowledgeBase' && props.latestRefs.knowledge_base) {
      console.log('Forcing knowledgeBase tab even though hasKnowledgeBaseData is false');
      activeTab.value = 'knowledgeBase';
    } else if (tab === 'multimodalKnowledgeBase' && props.latestRefs.multimodal_knowledge_base) {
      console.log('Forcing multimodalKnowledgeBase tab even though hasMultimodalKnowledgeBaseData is false');
      activeTab.value = 'multimodalKnowledgeBase';
    } else if (tab === 'multiRound' && props.latestRefs.multi_round) {
      console.log('Forcing multiRound tab even though hasMultiRoundData is false');
      activeTab.value = 'multiRound';
    } else if (tab === 'graph' && props.latestRefs.graph_base) {
      console.log('Forcing graph tab even though hasGraphData is false');
      activeTab.value = 'graph';
    }
  }
}

// 图表ref
const graphContainerRef = ref(null);

// 监听可见性变化后的处理
watch(() => props.visible, (visible) => {
  if (!visible) return;

  // 根据当前活动标签页执行相应的刷新操作
  nextTick(() => {
    if (activeTab.value === 'graph' && hasGraphData.value) {
      console.log('Refreshing graph after sidebar becomes visible');
      // 触发图表容器的重新布局
      if (graphContainerRef.value && graphContainerRef.value.refreshGraph) {
        graphContainerRef.value.refreshGraph();
      }
    }
  });
});

// 监听标签页变化
watch(activeTab, (newTab) => {
  if (newTab === 'graph' && hasGraphData.value && props.visible) {
    // 切换到图表标签时，需要延迟一下重新渲染图表
    nextTick(() => {
      if (graphContainerRef.value && graphContainerRef.value.refreshGraph) {
        graphContainerRef.value.refreshGraph();
      }
    });
  }
});

// 监视latestRefs的变化，便于调试
watch(() => props.latestRefs, (newRefs) => {
  console.log('RefsSidebar latestRefs changed:', {
    hasGraph: hasGraphData.value,
    hasWeb: hasWebSearchData.value,
    hasKB: hasKnowledgeBaseData.value,
    hasMultimodalKB: hasMultimodalKnowledgeBaseData.value,
    graph: newRefs.graph_base?.results?.nodes?.length,
    web: newRefs.web_search?.results?.length,
    kb: newRefs.knowledge_base?.results?.length,
    multimodalKb: newRefs.multimodal_knowledge_base?.results?.length
  });
}, { deep: true });

// 向父组件暴露方法
defineExpose({
  setActiveTab,
  togglePin
})
</script>

<style lang="less" scoped>
.refs-sidebar-container {
  position: fixed;
  // top: 8vh;
  right: calc(-1 * var(--refs-sidebar-floating-width)); /* 初始状态隐藏在右侧 - 使用更大宽度 */
  width: var(--refs-sidebar-floating-width); /* 未固定时的宽度 */
  height: 100vh;
  background: var(--surface);
  color: var(--text-primary);
  z-index: 1000;
  transition: right 0.3s ease, width 0.3s ease; /* 添加宽度过渡效果 */
  display: flex;
  flex-direction: column;
  user-select: text;

  &::before {
    z-index: 999; // 降低遮罩层的z-index
  }

  // 添加遮罩
  &::before {
    content: '';
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(0, 0, 0, 0.3);
    z-index: -1;
    opacity: 0;
    transition: opacity 0.3s;
    pointer-events: none;
  }

  &.visible:not(.pinned)::before {
    opacity: 1;
    pointer-events: auto;
    cursor: pointer;
  }

  &.visible {
    right: 0; /* 显示时移动到可见区域 */
  }

  &.pinned {
    width: var(--refs-sidebar-pinned-width); /* 固定时的宽度 */
    box-shadow: none;
    border-left: 1px solid var(--gray-200);
  }

  &.visible:not(.pinned) {
    box-shadow: -2px 0 15px rgba(0, 0, 0, 0.15);
    .refs-sidebar {
      background-color: var(--surface);
    }
  }

  .refs-sidebar {
    height: 100%;
    display: flex;
    flex-direction: column;
    // padding: 0 16px;
  }

  .sidebar-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid var(--gray-200);

    h3 {
      margin: 0;
      font-size: 16px;
      font-weight: 600;
    }

    .sidebar-controls {
      display: flex;
      align-items: center;

      .pin-button {
        margin-right: 8px;

        .pinned {
          color: var(--main-color);
          transform: rotate(45deg);
        }
      }
    }
  }

  .refs-tabs {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    padding: 0 16px;
  }

  .ant-tabs-content {
    flex: 1;
    overflow-y: auto;
  }

  .empty-data {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 200px;
    color: var(--text-secondary);
    font-size: 14px;
    background-color: var(--gray-100);
    border-radius: 4px;
  }

  .graph-container {
    min-height: 500px;
    width: 100%;
    display: flex;
    justify-content: center;
    align-items: center;
  }

  .fileinfo {
    display: flex;
    justify-content: space-between;
    padding: 12px 16px;
    background-color: var(--gray-100);
    border-radius: 4px;
    margin-bottom: 16px;

    p {
      margin: 0;
      color: var(--text-secondary);
    }
  }

  // 通用结果展示样式的LESS mixin
  .result-text-mixin() {
    font-size: 14px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--gray-800);
  }

  // 知识库结果样式
  .results-list {
    .result-item {
      border-bottom: 1px solid var(--gray-200);
      padding: 16px 0;

      &:last-child {
        border-bottom: none;
      }
    }

    .result-meta {
      margin-bottom: 12px;

      .score-info {
        display: flex;
        flex-wrap: wrap;
        gap: 2rem;
        margin-bottom: 8px;

        span {
          display: flex;
          align-items: center;

          strong {
            margin-right: 8px;
            white-space: nowrap;
            color: var(--gray-700);
          }

          .ant-progress {
            width: 170px;
            margin-bottom: 0;
            margin-inline: 10px;

            .ant-progress-bg {
              background-color: var(--gray-500);
            }
          }
        }
      }

      .result-id {
        font-size: 12px;
        color: var(--gray-600);
        margin-bottom: 8px;
      }
    }

    .result-content {
      .result-title {
        font-size: 16px;
        font-weight: bold;
        margin-bottom: 8px;
        color: var(--gray-800);
      }

      .result-url {
        font-size: 12px;
        color: var(--main-color);
        margin-bottom: 8px;

        a {
          color: var(--main-color);
          word-break: break-all;
        }
      }

      .result-text {
        .result-text-mixin();
        background-color: var(--gray-100);
        padding: 12px;
        border-radius: 4px;
        border: 1px solid var(--gray-200);
      }
    }
  }

  // 网页搜索结果卡片样式
  .web-results-list {
    display: flex;
    flex-direction: column;
    gap: 16px;

    .web-result-card {
      background-color: var(--surface-raised);
      border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
      transition: all 0.3s ease;
      border: 1px solid var(--gray-200);

      &:hover {
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
      }

      .card-header {
        padding: 16px 16px 0;

        .result-title {
          margin-bottom: 8px;
          line-height: 1.4;
        }
      }

      .card-body {
        padding: 0 16px;

        .result-text {
          .result-text-mixin();
          margin-bottom: 16px;
          max-height: 200px;
          overflow-y: auto;
        }
      }

      .card-footer {
        background-color: var(--gray-100);
        padding: 12px 16px;
        border-top: 1px solid var(--gray-200);

        .score-info {
          display: flex;
          justify-content: space-between;

          span {
            font-size: 13px;
            color: var(--gray-600);
          }
        }
      }
    }
  }

  .multimodal-kb-summary {
    background: var(--gray-100);
    border: 1px solid var(--gray-200);
    border-radius: 6px;
    padding: 12px;
    margin-bottom: 12px;
    color: var(--gray-800);
    font-size: 14px;
    line-height: 1.6;

    p {
      margin: 8px 0 0;
      color: var(--gray-700);
      word-break: break-word;
    }

    .status-error {
      color: var(--error-color);
      font-weight: 600;
    }
  }

  // 多轮检索过程样式
  .multi-round-list {
    .multi-round-round {
      border: 1px solid var(--gray-200);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 12px;
      background: var(--surface-raised);

      .round-header {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 8px;
        flex-wrap: wrap;

        .round-badge {
          background: var(--main-10);
          color: var(--main-600);
          border-radius: 4px;
          padding: 2px 8px;
          font-weight: 600;
          font-size: 13px;
        }

        .round-recall {
          font-size: 13px;
          color: var(--gray-700);
        }

        .round-verdict {
          font-size: 13px;
          font-weight: 600;

          &.verdict-ok {
            color: #52c41a;
          }

          &.verdict-no {
            color: var(--error-color);
          }
        }
      }

      .round-queries {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-bottom: 8px;

        .query-chip {
          background: var(--gray-100);
          border: 1px solid var(--gray-200);
          border-radius: 10px;
          padding: 2px 8px;
          font-size: 12px;
          color: var(--gray-800);
        }
      }

      .round-reason {
        font-size: 13px;
        color: var(--text-secondary);
        margin-bottom: 4px;
        word-break: break-word;
      }

      .round-keywords {
        font-size: 13px;
        color: var(--gray-700);
        word-break: break-word;
      }
    }

    .multi-round-final {
      font-size: 13px;
      color: var(--text-secondary);
      text-align: center;
      padding: 8px 0;
    }
  }

  @media (max-width: 768px) {
    width: 500px; /* 中等屏幕上未固定时的宽度 */
    right: -500px;

    &.pinned {
      width: 280px; /* 中等屏幕上固定时的宽度 */
    }
  }

  @media (max-width: 480px) {
    width: 100%; /* 小屏幕上的宽度 */
    right: -100%;

    &.pinned {
      width: 100%; /* 小屏幕上固定时也是全宽 */
    }
  }
}

.overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: -1;
}
</style>
