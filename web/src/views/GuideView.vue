<template>
  <a-modal 
    v-model:open="modalVisible"
    width="70%"
    :footer="null"
    :style="{ top: '7%' }"
    wrap-class-name="json-modal"
    class="json-modal"
  >
    <template #title>
      <div style="display: flex; justify-content: space-between; align-items: center; width: 95%;margin-bottom: 15px;">
        <span style="color: brown;font-weight: bold;font-size: large;">会议事项层级结构</span>
        <div class="search-bar">
          <a-input-search
            v-model:value="searchText"
            placeholder="搜索内容"
            enter-button="搜索"
            @search="searchInContent"
            style="width: 300px; margin-right: 10px;"
          />
          <a-button @click="prevResult" :disabled="searchResults.length === 0" style="display: inline-flex; align-items: center;gap: 5px;margin-left: 8px;">
            <template #icon>
              <PanelRightOpen style="font-size: 16px;" />
            </template>
            <span style="line-height: 1;">上一个</span>
          </a-button>

          <a-button @click="nextResult" :disabled="searchResults.length === 0" style="display: inline-flex; align-items: center;gap: 5px;margin-left: 8px;">
            <template #icon>
              <PanelLeftOpen style="font-size: 16px;" />
            </template>
            <span style="line-height: 1;">下一个</span>
          </a-button>
          <span v-if="searchResults.length > 0" style="margin-left: 10px;">
            {{ currentSearchIndex + 1 }} / {{ searchResults.length }}
          </span>
        </div>
      </div>
    </template>
    <pre class="json-content" v-html="highlightedContent"></pre>
  </a-modal>
  
  <!-- <a-tabs default-active-key="1" class="guide-tabs" type="card"> -->
    <!-- <a-tab-pane key="1" tab="议事决策"> -->
      <div class="guide-layout">
        <!-- 左侧 1/3 -->
        <div class="guide-left">
          <div class="guide-left-top">
            <!-- 查询示例展示区 -->
            <a-card 
              title="查询示例" 
              size="small" 
              class="guide-card"
            >
              <template #extra>
                <a-button 
                  type="text" 
                  size="small"
                  @click="isExampleCollapsed = !isExampleCollapsed"
                  :style="{ color: 'brown' }"
                >
                  {{ isExampleCollapsed ? '展开' : '收起' }}
                </a-button>
              </template>
              <div v-show="!isExampleCollapsed">
                <ul>
                  <li
                    v-for="(item, idx) in examples"
                    :key="idx"
                    @click="appendExample(item)"
                    style="cursor: pointer; color: #1677ff;"
                  >
                    {{ item }}
                  </li>
                </ul>
              </div>
            </a-card>
          </div>
          <div class="guide-left-bottom">
            <!-- 大模型文字输出区 -->
            <a-card title="模型输出" size="small" class="guide-card output-card" ref="outputCard">
                <!-- <h2>{{ msg.find(m => m.role === 'sent')?.content }}</h2> -->
                <a-divider style="margin: 4px 0;" />
                  <h3>📌 决策图构建:</h3>
                  <MdPreview v-show="!showHistoryPreview" :modelValue="modelOutputMd" :style="{ paddingLeft: '30px' }"/>
                  <MdPreview v-show="showHistoryPreview" :modelValue="modelHistoryOutputMd" :style="{ paddingLeft: '30px' }"/>
                 <!-- 加载中状态 -->
                <div v-if="isStreaming" class="loading-dots">
                  <div></div>
                  <div></div>
                  <div></div>
                </div>
            </a-card>
          </div>
        </div>
        <!-- 右侧 2/3 -->
        <div class="guide-right">
          <div class="guide-right-top">
            <!-- 输入区 -->
            <a-textarea
              v-model:value="inputText"
              placeholder="请输入内容"
              class="input-area"
            />
            <a-button type="primary" @click="startGuide" class="submit-btn" :disabled="isStreaming">开始引导</a-button>
            <a-button type="primary" @click="viewSourceFile" class="submit-btn">查看源文件</a-button>
            <a-button type="primary" @click="showHistory" class="submit-btn" :disabled="isStreaming">历史记录</a-button>
    <a-drawer
      title="历史记录"
      placement="right"
      :width="1000"
      :open="drawerVisible"
      @close="() => drawerVisible = false"
    >
      <a-list
        item-layout="vertical"
        size="large"
        :data-source="msgHistory"
      >
        <template #renderItem="{ item }">
          <a-list-item>
            <a-list-item-meta>
              <template #title>
                <div style="display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 8px 12px;">
                  <div 
                    style="flex: 1; font-weight: bold; color: brown; 
                           white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"
                    :title="item.sent"
                  >{{ item.sent }}</div>
                  <div style="font-weight: bold; color: brown; min-width: 120px;">{{ item.updatetime }}</div>
                  <div style="display: flex; gap: 8px;">
                    <a-button 
                      type="primary" 
                      @click="() => showHistoryGuideRecord(item)"
                      style="background: brown; color: white; border-color: brown;"
                    >查看该记录</a-button>
                    <a-button 
                      type="primary" 
                      danger
                      @click="() => delGuideRecord(item)"
                      style="background: brown; color: white; border-color: brown;"
                    >删除该记录</a-button>
                  </div>
                </div>
              </template>
            </a-list-item-meta>
            <a-collapse :default-active-key="['1']" :bordered="true">
              <a-collapse-panel key="1" :show-arrow="true">
                <template #header>
                  <span style="color: #666;">点击查看回答</span>
                </template>
                <pre style="white-space: pre-wrap; background: #f5f5f5; padding: 10px; border-radius: 4px; margin: 0;">{{ item.received }}</pre>
              </a-collapse-panel>
            </a-collapse>
          </a-list-item>
        </template>
      </a-list>
    </a-drawer>
          </div>
          <div class="guide-right-bottom">
            <!-- Mermaid 图展示区 -->
            <a-card title="Mermaid 图（ 学院会议议题结构 - 点击可放大）" size="small" class="guide-card mermaid-card">
              <div
                ref="mermaidContainer"
                class="mermaid-img"
                @click="toggleMermaidFullscreen"
              >
              </div>
            </a-card>
            <div v-if="isMermaidFullscreen" class="mermaid-fullscreen" @click="toggleMermaidFullscreen">
              <div
                ref="fullscreenMermaidContainer"
                class="mermaid-img"
                :style="{ transform: `scale(${scale})` }"
              >
              </div>
            </div>
          </div>
        </div>
      </div>
    <!-- </a-tab-pane> -->
    <!-- <a-tab-pane key="2" tab="历史记录">
      <div style="padding: 24px;">历史记录内容...</div>
    </a-tab-pane>
  </a-tabs> -->
</template>

<script setup>
import { ref, onMounted, nextTick, reactive, watch, computed } from 'vue';
import { message } from 'ant-design-vue';
import '@/assets/highlight.css';
import meetingStructure from '@/assets/会议事项层级结构.json'
import meetingStructureSource from '@/assets/会议事项层级结构_copy.json'
import { MdPreview } from 'md-editor-v3';
import 'md-editor-v3/lib/preview.css';
import dayjs from 'dayjs';

import { Ellipsis, PanelLeftOpen, PanelRightOpen, MessageSquarePlus, Compass, Waypoints, BookCheck, Search } from 'lucide-vue-next'
import { onClickOutside } from '@vueuse/core'
import { useConfigStore } from '@/stores/config'
import { useUserStore } from '@/stores/user'
import MessageInputComponent from '@/components/MessageInputComponent.vue'
import MessageComponent from '@/components/MessageComponent.vue'
import RefsSidebar from '@/components/RefsSidebar.vue'
import { chatApi } from '@/apis/auth_api'
import { guideRecordApi } from '@/apis/auth_api'


const inputText = ref('');
const mermaidContainer = ref(null);
const fullscreenMermaidContainer = ref(null);
const outputCard = ref(null);
const scale = ref(1.5); // 默认放大1.5倍
const isMermaidFullscreen = ref(false);
const isStreaming = ref(false);
const isExampleCollapsed = ref(false);
const showHistoryPreview = ref(false);
const modelHistoryOutputMd = ref('');

//源文件查看相关
const modalVisible = ref(false);
const jsonContent = ref('');
const searchText = ref('');
const searchResults = ref([]);
const currentSearchIndex = ref(-1);
const highlightedContent = ref('');

const examples = ref([
  '关于学院年度财务预算决算的审定和执行事项应该提交哪个会议？',
  '教师职称评定事项需要怎么决策？',
  '学院党建工作规划的制定应该由哪个会议讨论决定？',
  '学生表彰奖励事项需要怎么决策？'
])

const mermaidCode = ref(`
flowchart LR
  Start[提交会议-示例]
  
  Start --> A[党委会]
  Start --> B[党政联席会议]
  
  %% 党委会分支
  A --> A1[讨论决定]
  A --> A2[先行把关]
  
  A1 --> A1a[学院党建工作规划的制定？]
  A1a --> A1a_cat[📂 分类]
  A1a --> A1a_analysis[🧠 分析]

  A1a_cat --> A1a1["党的建设事项 →\n 党建工作规划、年度工作计划、改革举措、规章制度的制定和修订\n 学院党建工作规划（1-3-1）"]
  A1a_analysis --> A1a2["根据《会议事项层级结构》，学院党建工作规划属于\n党建工作的重要事项， 应由党委会讨论决定。"]

  A2 --> A2a[教师职称评定事项？]
  A2a --> A2a_cat[📂 分类]
  A2a --> A2a_analysis[🧠 分析]

  A2a_cat --> A2a1["事关教师队伍建设的事项 →\n 教职员工的聘用、调动、晋升、考核、职称职级评定中的重要事项\n 教职员工职称职级评定（5-2-3）"]
  A2a_analysis --> A2a2["根据《会议事项层级结构》，教师职称评定事项属于该类重要事项，\n 需提交党委会先行把关，同时提交党政联席会议讨论决定。"]

  %% 党政联席会议分支
  B --> B1[讨论决定]
  B1 --> B1a[教师职称评定事项？]
  B1a --> B1a_cat[📂 分类]
  B1a --> B1a_analysis[🧠 分析]

  B1a_cat --> B1a1["事关教师队伍建设的事项 →\n 教职员工的聘用、调动、晋升、考核、职称职级评定、薪酬分配中的重要事项\n 教职员工职称职级评定（5-2-3）"]
  B1a_analysis --> B1a2["根据《会议事项层级结构》，教师职称评定事项\n 需提交党委会把关，并提交党政联席会议讨论决定。"]
`);

const msg = reactive([
  {
    id: 'default-msg',
    role: 'received',
    content: mermaidCode.value,
    reasoning_content: '',
    refs: '',
    status: "init",
    meta: {},
    showThinking: "show"
  }
])
const msgHistory = reactive([])

const meta = reactive({
  use_graph: false,
  use_web: false,
  graph_name: "neo4j",
  selectedKB: null,
  summary_title: false,
  history_round: 20,
  db_id: null,
  fontSize: 'default',
  wideScreen: false,
})

const startGuide = () => {
  isExampleCollapsed.value = true;
  showHistoryPreview.value = false;
  sendMessage();
  message.success('提交成功！');
}
const viewSourceFile = () => {
  modalVisible.value = true;
  const rawContent = JSON.stringify(meetingStructureSource, null, 2)
    .replace(/"([^"]+)":/g, '$1:')
    .replace(/\\n/g, '\n');
  jsonContent.value = rawContent;
  highlightedContent.value = rawContent;
}
const showHistory = () => {
  getGuideRecords();
  drawerVisible.value = true
}
const showHistoryGuideRecord = (item) => {
  drawerVisible.value = false
  showHistoryPreview.value = true;
  inputText.value = item.sent
  mermaidCode.value = item.received;
  modelHistoryOutputMd.value = item.received;
  loadMermaidAndRender();
}

const modelOutputMd = computed(() => {
  const receivedMsg = msg.find(m => m.role === 'received')
  return '\n' + (receivedMsg?.content.replace(/^```mermaid\s*/i, '').replace(/```$/, '').trim() || '')
})


const searchInContent = () => {
  if (!searchText.value.trim()) {
    message.warning('请输入搜索内容');
    return;
  }

  const regex = new RegExp(searchText.value, 'gi');
  const matches = [];
  let match;
  
  while ((match = regex.exec(jsonContent.value)) !== null) {
    matches.push({
      start: match.index,
      end: match.index + match[0].length
    });
  }

  searchResults.value = matches;
  currentSearchIndex.value = -1;

  if (matches.length === 0) {
    message.warning('未找到匹配内容');
    highlightedContent.value = jsonContent.value;
    return;
  }

  highlightAndScroll(0);
}

const highlightAndScroll = (index) => {
  if (index < 0 || index >= searchResults.value.length) return;

  currentSearchIndex.value = index;
  const result = searchResults.value[index];
  
  // 创建新的正则表达式，确保全局匹配
  const regex = new RegExp(searchText.value, 'gi');
  let match;
  const matches = [];
  
  // 先找到所有匹配项
  while ((match = regex.exec(jsonContent.value)) !== null) {
    matches.push({
      text: match[0],
      index: match.index
    });
  }

  // 构建高亮内容
  let highlighted = '';
  let lastIndex = 0;
  
  matches.forEach((match, i) => {
    // 添加非匹配部分
    if (match.index > lastIndex) {
      highlighted += jsonContent.value.slice(lastIndex, match.index);
    }
    
    // 添加匹配部分
    const isActive = i === index;
    highlighted += isActive 
      ? `<mark class="highlight active-highlight">${match.text}</mark>`
      : `<span class="highlight">${match.text}</span>`;
    lastIndex = match.index + match.text.length;
  });
  
  // 添加剩余部分
  if (lastIndex < jsonContent.value.length) {
    highlighted += jsonContent.value.slice(lastIndex);
  }

  highlightedContent.value = highlighted;

  // 滚动到当前匹配项
  nextTick(() => {
    const preElement = document.querySelector('.json-content');
    if (!preElement) return;
    
    const activeElement = preElement.querySelector('mark.highlight.active-highlight');
    if (activeElement) {
      // 强制重绘
      activeElement.offsetHeight;
      
      // 添加动画效果
      activeElement.classList.add('highlight-animate');
      setTimeout(() => {
        activeElement.classList.remove('highlight-animate');
      }, 500);
      
      activeElement.scrollIntoView({
        behavior: 'smooth',
        block: 'center'
      });
    }
  });
}

const nextResult = () => {
  if (searchResults.value.length === 0) return;
  const nextIndex = (currentSearchIndex.value + 1) % searchResults.value.length;
  highlightAndScroll(nextIndex);
}

const prevResult = () => {
  if (searchResults.value.length === 0) return;
  const prevIndex = (currentSearchIndex.value - 1 + searchResults.value.length) % searchResults.value.length;
  highlightAndScroll(prevIndex);
}
const drawerVisible = ref(false)

const appendExample = (text) => {
  inputText.value = text;
}

const generateRandomHash = (length) => {
    let chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    let hash = '';
    for (let i = 0; i < length; i++) {
        hash += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return hash;
}

const appendAiMessage = (content) => {
  msg.push({
    id: generateRandomHash(16),
    role: 'received',
    content: content,
    reasoning_content: '',
    refs:'',
    status: "init",
    meta: {},
    showThinking: "show"
  })
}

const appendUserMessage = (content) => {
  msg.push({
    id: generateRandomHash(16),
    role: 'sent',
    content: content
  })
}

const sendMessage = () => {
  const user_input = inputText.value.trim();
  if (isStreaming.value) {
    message.error('请等待上一条消息处理完成');
    return
  }
  if (user_input) {
    // 使用.length来清空元素更方便，用reactive声明的没有.value方法
    msg.length = 0;
    isStreaming.value = true;
    appendUserMessage(user_input);
    appendAiMessage("");
    fetchChatResponse(user_input)
  } else {
  }
}

const fetchChatResponse = (user_input) => {
  const controller = new AbortController();
  const signal = controller.signal;

  const params = {
    query: systemPrompt + " 以下是用户输入： " + user_input,
    history: [],
    meta: meta,
  }

  // 使用API函数发送请求
  chatApi.sendMessageWithAbort(params, signal)
  .then((response) => {
    if (!response.ok) {
      // 检查是否是401错误（令牌过期）
      if (response.status === 401) {
        const userStore = useUserStore();
        if (userStore.isLoggedIn) {
          message.error('登录已过期，请重新登录');
          userStore.logout();

          // 使用setTimeout确保消息显示后再跳转
          setTimeout(() => {
            window.location.href = '/login';
          }, 1500);
        }
        throw new Error('未授权，请先登录');
      }
      throw new Error(`请求失败: ${response.status} ${response.statusText}`);
    }

    if (!response.body) throw new Error("ReadableStream not supported.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = '';

    const readChunk = () => {
      return reader.read().then(({ done, value }) => {
        if (done) {
          updateCurrentMessage({showThinking: "no"});
          isStreaming.value = false;
          return;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');

        // 处理除最后一行外的所有完整行
        for (let i = 0; i < lines.length - 1; i++) {
          const line = lines[i].trim();
          if (line) {
            try {
              const data = JSON.parse(line);
              updateCurrentMessage({
                content: data.response,
                reasoning_content: data.reasoning_content,
                status: data.status,
                meta: data.meta,
                ...data,
              });

              // if (data.history) {
              //   conv.value.history = data.history;
              // }
            } catch (e) {
              console.error('JSON 解析错误:', e, line);
            }
          }
        }

        // 保留最后一个可能不完整的行
        buffer = lines[lines.length - 1];

        return readChunk(); // 继续读取
      });
    };
    readChunk();
  })
  .catch((error) => {
    if (error.name === 'AbortError') {
    } else {
      console.error('聊天请求错误:', error);

      // 检查是否是认证错误
      if (error.message.includes('未授权') || error.message.includes('令牌已过期')) {
        // 已在上面处理，这里不需要重复处理
      } else {
        updateCurrentMessage({
          status: "error",
          message: error.message || '请求失败',
        });
      }
    }
    isStreaming.value = false;
  });

  // 监听 isStreaming 变化，当为 false 时中断请求
  watch(isStreaming, (newValue) => {
    if (!newValue) {
      controller.abort();
    }
  });
}

const updateCurrentMessage = (info) => {
  // const msg = conv.value.messages.find((msg) => msg.id === info.id);
  const aiMsg = msg.find((m) => m.role === "received");
  if (aiMsg) {
    try {
      // 特殊处理：content需要追加而不是替换
      if (info.content != null && info.content !== '') {
        // 检查新内容中是否有<think>标签
        if (info.content.includes('<think>') && !aiMsg.isCollectingThinking) {
          // 开始收集思考内容
          aiMsg.isCollectingThinking = true;

          // 分割内容，获取标签前后的部分
          const parts = info.content.split('<think>');
          aiMsg.content += parts[0]; // 添加标签前的内容到正文

          // 如果有标签后的内容，添加到思考内容
          if (parts.length > 1) {
            if (parts[1].includes('</think>')) {
              const thinkParts = parts[1].split('</think>');
              aiMsg.reasoning_content = (aiMsg.reasoning_content || '') + thinkParts[0];
              aiMsg.content += thinkParts[1]; // 添加结束标签后的内容到正文
              aiMsg.isCollectingThinking = false;
            } else {
              aiMsg.reasoning_content = (aiMsg.reasoning_content || '') + parts[1];
            }
          }
        }
        // 检查是否正在收集思考内容
        else if (aiMsg.isCollectingThinking) {
          if (info.content.includes('</think>')) {
            const parts = info.content.split('</think>');
            aiMsg.reasoning_content = (aiMsg.reasoning_content || '') + parts[0];
            aiMsg.content += parts[1]; // 添加结束标签后的内容到正文
            aiMsg.isCollectingThinking = false;
          } else {
            aiMsg.reasoning_content = (aiMsg.reasoning_content || '') + info.content;
          }
        }
        // 不在收集思考内容，正常追加
        else {
          aiMsg.content += info.content;
        }
      }

      // 批量处理其他属性，只有当属性值不为null/undefined且不为空字符串时才更新
      const propertiesToUpdate = [
        'reasoning_content', 'model_name', 'status', 'message', 'showThinking', 'refs', 'meta'
      ];

      propertiesToUpdate.forEach(prop => {
        if (info[prop] != null && (typeof info[prop] !== 'string' || info[prop] !== '')) {
          aiMsg[prop] = info[prop];

          // 如果更新了refs，同时更新全局refs
          if (prop === 'refs' && info.refs) {
            currentRefs.value = info.refs;
          }
        }
      });
    } catch (error) {
      console.error('Error updating message:', error);
      aiMsg.status = 'error';
      aiMsg.content = '消息更新失败';
    }
  } else {
    console.error('Message not found:', info);
  }
};

// 数据库相关/保存引导记录
const saveGuideRecord = async (currMsg) => {
  try {
    await guideRecordApi.saveGuideRecords(currMsg)
    message.success('保存引导记录成功');
  } catch (e) {
    message.error('保存引导记录失败', e.message || '未知错误');
  }
}

const getGuideRecords = async () => {
  try {
    const res = await guideRecordApi.getGuideRecords();
    if (res.length !== 0) {
      const updated = res.map(record => ({
        ...record.content,
        updatetime: dayjs(record.updatetime).format('YYYY-MM-DD HH:mm:ss')
      }));
      msgHistory.splice(0, msgHistory.length, ...updated);
      // message.success('获取引导记录成功');
    }
  } catch (e) {
    message.error('获取记录失败', e.message || '未知错误');
  }
}

const delGuideRecord = async (delMsg) => {
  try {
    await guideRecordApi.deleteGuideRecord(delMsg.id)
    message.success('删除引导记录成功');
    // 删除后重新获取记录
    getGuideRecords();
  } catch (e) {
    message.error('删除引导记录失败', e.message || '未知错误');
  }
}

// 动态加载 mermaid CDN 并渲染
async function loadMermaidAndRender() {
  async function render(container) {
    if (!window.mermaid) {
      console.error("Mermaid script not loaded or initialized yet.");
      return;
    }
    if (container.value) {
      container.value.innerHTML = ''; // Clear previous diagram
      const mermaidDiv = document.createElement('div');
      mermaidDiv.className = 'mermaid';
      mermaidDiv.textContent = mermaidCode.value.replace(/^```mermaid\s*/i, '').replace(/```$/, '').trim();
      container.value.appendChild(mermaidDiv);
      try {
        await window.mermaid.run({ nodes: [mermaidDiv] });
        // After Mermaid renders, set the SVG to be responsive
        nextTick(() => {
          const svg = container.value.querySelector('svg');
          if (svg) {
            svg.style.width = '100%';
            svg.style.height = 'auto';
          }
        });
      } catch (e) {
        console.error('Mermaid render error:', e);
      }
    }
  }

  // Render the main diagram
  await render(mermaidContainer);
  // If in fullscreen, render that one too
  if (isMermaidFullscreen.value) {
    await render(fullscreenMermaidContainer);
  }
}

onMounted(() => {
  // Load the mermaid script dynamically
  const script = document.createElement('script');
  script.src = '/src/assets/mermaid.min.js';
  script.onload = () => {
    // Once the script is loaded, initialize Mermaid
    window.mermaid.initialize({
      startOnLoad: false,
      theme: 'default',
      themeVariables: {
        fontFamily: 'Arial',
        fontSize: '24px'
      }
    });
    // Use nextTick to ensure Vue has updated the DOM before we try to render
    nextTick(() => {
      loadMermaidAndRender();
    });
  };
  document.head.appendChild(script);
  
  getGuideRecords();
});

onMounted(() => {
  // Load the mermaid script dynamically
  const script = document.createElement('script');
  script.src = '/src/assets/mermaid.min.js';
  script.onload = () => {
    // Once the script is loaded, initialize Mermaid
    window.mermaid.initialize({
      startOnLoad: false,
      theme: 'default',
      themeVariables: {
        fontFamily: 'Arial',
        fontSize: '24px'
      }
    });
    // Use nextTick to ensure Vue has updated the DOM before we try to render
    nextTick(() => {
      loadMermaidAndRender();
    });
  };
  document.head.appendChild(script);
  
  getGuideRecords();
});

onMounted(() => {
  // Load the mermaid script dynamically
  const script = document.createElement('script');
  script.src = '/src/assets/mermaid.min.js';
  script.onload = () => {
    // Once the script is loaded, initialize Mermaid
    window.mermaid.initialize({
      startOnLoad: false,
      theme: 'default',
      themeVariables: {
        fontFamily: 'Arial',
        fontSize: '24px'
      }
    });
    // Use nextTick to ensure Vue has updated the DOM before we try to render
    nextTick(() => {
      loadMermaidAndRender();
    });
  };
  document.head.appendChild(script);
  
  getGuideRecords();
});

// 自动滚动到底部
watch(modelOutputMd, () => {
    nextTick(() => {
        const outputElement = document.querySelector('.output-card .ant-card-body');
        if (outputElement) {
            outputElement.scrollTop = outputElement.scrollHeight;
        }
    });
}, { immediate: true });

const toggleMermaidFullscreen = () => {
    isMermaidFullscreen.value = !isMermaidFullscreen.value;
    if (isMermaidFullscreen.value) {
        document.body.style.overflow = 'hidden';
        // 重新渲染mermaid图以适应全屏
        nextTick(() => {
            loadMermaidAndRender();
            // 添加滚轮缩放事件
            const container = document.querySelector('.mermaid-fullscreen .mermaid-img');
            if (container) {
                container.addEventListener('wheel', handleWheelZoom, { passive: false });
            }
        });
    } else {
        document.body.style.overflow = '';
        // 移除滚轮缩放事件
        const container = document.querySelector('.mermaid-fullscreen .mermaid-img');
        if (container) {
            container.removeEventListener('wheel', handleWheelZoom);
        }
        // 重置缩放比例
        scale.value = 1;
    }
};

const handleWheelZoom = (e) => {
    e.preventDefault();
    const delta = -e.deltaY;
    const zoomIntensity = 0.8; // 进一步增加缩放强度
    
    // 计算新的缩放比例 (指数缩放更自然)
    let newScale = scale.value * Math.pow(1.2, delta > 0 ? 1 : -1);
    
    // 限制最小和最大缩放 (0.5x - 8x)
    newScale = Math.max(0.5, Math.min(newScale, 8));
    
    scale.value = newScale;
};

// 监听received消息状态变化重新渲染mermaid图
watch(() => msg.find(m => m.role === 'received')?.status, (newStatus) => {
  if (newStatus === 'finished') {
    const receivedMsg = msg.find(m => m.role === 'received');
    if (receivedMsg?.content) {
      mermaidCode.value = receivedMsg.content;
      loadMermaidAndRender();
    }
  }
});

// 监听received消息状态变化存储历史记录msgHistory
watch(() => msg.find(m => m.role === 'received')?.status, (newStatus) => {
  if (newStatus === 'finished') {
    const receivedMsg = msg.find(m => m.role === 'received');
    const sentMsg = msg.find(m => m.role === 'sent');
    if (receivedMsg?.content && sentMsg?.content) {
      const currMsg = { 
        id: generateRandomHash(16),
        received: receivedMsg.content, 
        sent: sentMsg.content 
      };
      msgHistory.push(currMsg);
      saveGuideRecord(currMsg);
    }
  }
});


// 构建系统提示词
const systemPrompt = `你是学院议事规则专家，任务是依据《会议事项层级结构》判断用户提出的议题应提交给哪些会议审议，并确定相应的决策类型。请严格遵循以下规则进行分析和回答：

决策流程规则：

1.确定议题所属大类（如党的建设、改革发展稳定、内部治理等）
2.逐级匹配子类与具体事项，直到找到最符合的条目
3.根据匹配事项对应的字段，给出需要提交的会议类型及其决策类型
4.如事项涉及多个会议（如既需党委会又需党政联席会），请分别列出每个会议及其对应的决策类型
5.《会议事项层级结构》中，“党委会”与“党政联席会”对应的决策类型编码含义如下： 0:讨论决定 1:研究落实 2:先行把关

以下是《会议事项层级结构》内容：
${JSON.stringify(meetingStructure, null, 2)}

示例：
用户输入：学院党建工作规划的制定应该由哪个会议讨论决定？教师职称评定事项需要怎样决策？
请用以下格式回答：

flowchart LR
  Start[提交会议]
  
  Start --> A[党委会]
  Start --> B[党政联席会议]
  
  %% 党委会分支
  A --> A1[讨论决定]
  A --> A2[先行把关]
  
  A1 --> A1a[学院党建工作规划的制定？]
  A1a --> A1a_cat[📂 分类]
  A1a --> A1a_analysis[🧠 分析]
  
  A1a_cat --> A1a1["党的建设事项 →\n 党建工作规划、年度工作计划、改革举措、规章制度的制定和修订\n 学院党建工作规划（1-3-1）"]
  A1a_analysis --> A1a2["根据《会议事项层级结构》，学院党建工作规划属于党建工作的重要事项，\n 应由党委会讨论决定。"]

  A2 --> A2a[教师职称评定事项？]
  A2a --> A2a_cat[📂 分类]
  A2a --> A2a_analysis[🧠 分析]

  A2a_cat --> A2a1["事关教师队伍建设的事项 →\n 教职员工的聘用、调动、晋升、考核、职称职级评定中的重要事项\n 教职员工职称职级评定（5-2-3）"]
  A2a_analysis --> A2a2["根据《会议事项层级结构》，教师职称评定事项属于该类重要事项，\n 需提交党委会先行把关，同时提交党政联席会议讨论决定。"]

  %% 党政联席会议分支
  B --> B1[讨论决定]
  B1 --> B1a[教师职称评定事项？]
  B1a --> B1a_cat[📂 分类]
  B1a --> B1a_analysis[🧠 分析]

  B1a_cat --> B1a1["事关教师队伍建设的事项 →\n 教职员工的聘用、调动、晋升、考核、职称职级评定、薪酬分配中的重要事项\n 教职员工职称职级评定（5-2-3）"]
  B1a_analysis --> B1a2["根据《会议事项层级结构》，教师职称评定事项需提交党委会把关，\n 并提交党政联席会议讨论决定。"]

注意事项：
  - 请勿输出除以上格式以外的内容
  - 请勿自行扩展或解释背景信息
  - 若用户的输入与任何无关，没有提供具体的议题或事项，或者在结构中找不到匹配项，不需要其他解释，直接输出以下内容:
      flowchart LR
      Start[提交会议-未找到匹配项]
      Start --> A[党委会]
      Start --> B[党政联席会议]
`;
</script>

<style lang="less" scoped>
.guide-layout {
  display: flex;
  height: 100%;
  gap: 16px;
  box-sizing: border-box;
  background: #fff;
  padding: 20px;
  overflow: hidden;
  .ant-tabs-nav {
    margin-bottom: 0;
  }
}
.guide-left {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 260px;
}
.guide-left-top{
  width: 100%;
  transition: all 0.3s ease;
}
.guide-left-bottom {
  overflow-y: hidden;
  transition: all 0.3s ease;
}
.guide-right {
  flex: 2.4;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 400px;
}
.guide-right-top {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.input-area {
  flex: 1;
  height: 38px;
  resize: none; 
}
.submit-btn {
  margin-left: 8px;
  height: 38px;
}
.guide-right-bottom {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.guide-card {
  width: 100%;
  height: 100%;
  font-size: 15px;
  font-weight: bold;
  overflow-y: auto;
  .ant-card-body {
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    padding: 12px;
  }
  .ant-card-head-title {
    font-weight: bold;
    font-size: 17px;
  }
  :deep(.ant-card-head-title) {
    font-weight: bold;
    font-size: 15px;
    color: brown;
  }
}
.mermaid-card {
  .mermaid-img {
    width: 100%;
    height: 100%;
    min-height: 200px;
    overflow: auto;
    position: relative;
    /* 放大缩小动画 */
    transition: transform 0.2s;
    cursor: pointer;
  }
}

.mermaid-fullscreen {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(255, 255, 255, 0.98);
  z-index: 9999;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 0;
  box-sizing: border-box;
  cursor: zoom-out;
  
  .mermaid-img {
    width: 100%;
    height: 100%;
    transform: scale(v-bind(scale));
    display: flex;
    justify-content: center;
    align-items: center;
    transition: transform 0.05s cubic-bezier(0.25, 0.1, 0.25, 1);
    overflow: auto;
    svg {
      width: auto;
      height: auto;
      max-width: none;
      max-height: none;
      transform-origin: center center;
    }
  }
}

.output-card {
  .ant-card-body {
    overflow-y: auto;
    max-height: calc(100% - 56px);
  }
}

.json-modal {
  .ant-modal-body {
    padding: 16px;
  }
}

.json-content {
  max-height: 70vh;
  overflow: auto;
  white-space: pre-wrap;
  font-family: 'Courier New', Courier, monospace;
  font-size: 16px;
  line-height: 1.5;
  color: #000000;
  background-color: #f8f8f8;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.search-bar {
  display: flex;
  align-items: center;
}


.loading-dots {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  left: 45%;

  div {
    width: 8px;
    height: 8px;
    margin: 0 4px;
    background-color: var(--gray-700);
    border-radius: 50%;
    opacity: 0.3;
    animation: pulse 0.5s infinite ease-in-out both;

    &:nth-child(1) {
      animation-delay: -0.32s;
    }

    &:nth-child(2) {
      animation-delay: -0.16s;
    }
  }
}
@keyframes pulse {
  0%, 80%, 100% {
    transform: scale(0);
    opacity: 0.3;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

</style>
