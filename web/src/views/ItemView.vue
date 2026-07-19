<template>
  <div class="item-container">
    <div class="item-content">
      <div class="sider" v-if="state.windowWidth > 520">
        <div class="sider-title" @click="state.section='intro'"><FlagOutlined /><h2>党建学习</h2></div>
        <a-button type="text" :class="{ activesec: state.section === 'knowledge'}" @click="state.section='knowledge'" :icon="h(BookOutlined)">
          题源知识库
        </a-button>
        <a-button type="text" :class="{ activesec: state.section === 'generate'}" @click="state.section='generate'" :icon="h(FormOutlined)">
          题目生成
        </a-button>
        <a-button type="text" :class="{ activesec: state.section === 'results'}" @click="state.section='results'" :icon="h(CheckCircleOutlined)">
          生成结果
        </a-button>
        <a-button type="text" :class="{ activesec: state.section === 'questions'}" @click="state.section='questions'" :icon="h(OrderedListOutlined)">
          出题记录
        </a-button>
        <a-button type="text" :class="{ activesec: state.section === 'store'}" @click="state.section='store'" :icon="h(DatabaseOutlined)">
          历史题库
        </a-button>
        <a-button type="text" :class="{ activesec: state.section === 'examination'}" @click="state.section='examination'" :icon="h(FundProjectionScreenOutlined)">
          考试测试
        </a-button>
        <a-button type="text" :class="{ activesec: state.section === 'AIassistant'}" @click="state.section='AIassistant'" :icon="h(RedditOutlined)">
          AI 助教
        </a-button>
      </div>

      <!-- 宣传介绍模块 -->
      <div class="content intro" v-if="state.windowWidth <= 520 || state.section === 'intro'">
        <div class="intro-header">
          <!-- <h3>党建智能学习平台</h3> -->
          <p class="subtitle">党建知识学习与题目生成系统</p>
        </div>
        
        <div class="intro-features">
          <div class="feature-card" v-for="(feature, index) in features" :key="index" 
               :style="{ 'animation-delay': `${index * 0.1}s` }">
            <div class="feature-icon">
              <component :is="feature.icon" />
            </div>
            <h4>{{ feature.title }}</h4>
            <p>{{ feature.desc }}</p>
          </div>
        </div>
        
        <div class="intro-highlights">
          <h4><StarOutlined /> 平台特色</h4>
          <div class="highlight-grid">
            <div class="highlight-item" v-for="(highlight, index) in highlights" :key="index">
              <div class="highlight-icon">
                <CheckCircleOutlined />
              </div>
              <span>{{ highlight }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 题目生成模块 -->
      <div class="content" v-if="state.windowWidth <= 520 || state.section === 'generate'">
        <div class="content-header">
          <h3>生成配置</h3>
          <div class="header-actions">
            <a-button 
              type="primary" 
              @click="generateQuestions" 
              class="generate-button"
              :loading="isGenerating || loadingDatabase"
            >
              开始生成
            </a-button>
            <a-button 
              @click="showPromptTemplate"
              class="prompt-button"
            >
              查看提示词
            </a-button>
          </div>
        </div>
        <div class="form-grid">
          <div class="form-item">
            <label>题目类型:</label>
            <a-select v-model:value="questionForm.type" style="width: 60%">
              <a-select-option value="单选题">单选题</a-select-option>
              <a-select-option value="多选题">多选题</a-select-option>
              <a-select-option value="判断题">判断题</a-select-option>
              <a-select-option value="简答题">简答题</a-select-option>
            </a-select>
          </div>
        
          <!-- <div class="form-item">
            <label>选择题选项数量:</label>
            <a-select v-model:value="questionForm.optionsNum" style="width: 60%">
              <a-select-option value="4">4</a-select-option>
              <a-select-option value="3">3</a-select-option>
              <a-select-option value="2">2</a-select-option>
            </a-select>
          </div> -->

          <div class="form-item">
            <label>题目数量:</label>
            <div class="slider-input-container">
              <a-input-number 
                v-model:value="questionForm.count" 
                :min="1" 
                :max="20" 
                style="width: 60%"
              />
            </div>
          </div>

          <div class="form-item">
            <label>难度级别:</label>
            <a-select v-model:value="questionForm.difficulty" style="width: 60%">
              <a-select-option value="简单">简单</a-select-option>
              <a-select-option value="中等">中等</a-select-option>
              <a-select-option value="困难">困难</a-select-option>
            </a-select>
          </div>

          <div class="form-item">
            <label>答案解析:</label>
            <a-select v-model:value="questionForm.analysis" style="width: 60%">
              <a-select-option value="yes">是</a-select-option>
              <a-select-option value="no">否</a-select-option>
            </a-select>
          </div>

          <div class="form-item">
            <label>生成模型:</label>
            <a-select 
              v-model:value="questionForm.modelName" 
              style="width: 60%"
              :options="modelOptions"
            />
          </div>

          <div class="form-item">
            <label>知识库:</label>
            <a-select v-model:value="targetDatabase.db_id" style="width: 60%" @change="handleChange">
              <a-select-option v-for="(db, index) in opts.databases" :key="index" :value="db.db_id">
                {{ db.name }}
              </a-select-option>
            </a-select>
          </div>

          <div class="form-item full-width">
            <label>关键词或关键句:</label>
            <a-textarea 
              v-model:value="questionForm.keywords" 
              placeholder="输入关键词，用逗号分隔" 
              :auto-size="{ minRows: 2, maxRows: 4 }"
            />
          </div>
        </div>

      </div>

      <!-- 知识库管理模块 -->
      <div class="content knowledge" v-if="state.windowWidth <= 520 || state.section === 'knowledge'">
        <template v-if="loadingDatabase">
          <div class="loading-container">
            <a-spin tip="加载知识库中..." />
          </div>
        </template>
        <template v-else-if="targetDatabase && targetDatabase.db_id">
          <ItemDataBaseInfoView 
            :database_id="targetDatabase.db_id"
            :key="targetDatabase.db_id"
            ref="databaseInfoView"
          />
        </template>
        <template v-else>
          <a-alert type="error" message="知识库加载失败" />
        </template>
      </div>

      <!-- 题目结果模块 -->
      <div class="content" v-if="state.windowWidth <= 520 || state.section === 'results'">
        <div class="content-header">
          <h3>题目结果</h3>
          <div class="results-actions" v-if="questionResults">
            <a-button 
              type="primary" 
              @click="saveToHistory" 
              :disabled="isGenerating"
              class="save-button"
            >
              保存至题库
            </a-button>
            <a-button 
              @click="clearResults"
              class="clear-button"
              :disabled="isGenerating"
            >
              清空
            </a-button>
          </div>
        </div>
        <div class="question-results-container">
          <div class="question-results" v-if="questionResults">
            <MdPreview :modelValue="questionResults" />
          </div>
          <div v-else-if="isGenerating" class="loading-prompt">
            <a-spin size="large" tip="题目生成中..." />
          </div>
          <div v-else class="empty-prompt">
            <p>题目生成后将显示在这里</p>
          </div>
        </div>
      </div>

      <!-- 历史记录模块 -->
      <div class="content" v-if="state.windowWidth <= 520 || state.section === 'questions'">
        <div class="content-header">
          <h3>题目生成历史记录</h3>
          <div class="results-actions">
            <a-button 
              @click="refreshHistory"
              :loading="isRefreshing"
            >
              刷新
            </a-button>
          </div>
        </div>
        <a-table
          :columns="historyColumns"
          :data-source="historyQuestions"
          row-key="id"
          size="small"
          bordered
          :pagination="{ pageSize: 10 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'createdAt'">
              {{ record.createdAt }}
            </template>
            <template v-else-if="column.key === 'type'">
              <a-tag :color="getTypeColor(record.type)">
                {{ record.type }}
              </a-tag>
            </template>
            <template v-else-if="column.key === 'action'">
              <a-button type="link" @click="showQuestionDetail(record)">
                查看
              </a-button>
              <a-button 
                type="link" 
                danger
                @click="delItemRecord(record.id)"
              >
                删除
              </a-button>
              <a-button 
                type="link" 
                style="color: green"
                @click="handleIndexClick(record)"
                :loading="isIndexing && !record.structured_content">
                {{ record.structured_content ? '已索引(点击查看)' : '索引' }}
              </a-button>
            </template>
          </template>
        </a-table>
      </div>

      <!-- 题库模块 -->
      <div class="content" v-if="state.windowWidth <= 520 || state.section === 'store'">
        <div class="content-header">
          <h3>题库</h3>
          <div class="results-actions">
            <a-button @click="refreshHistory" :loading="isRefreshing">
              刷新
            </a-button>
          </div>
        </div>

        <!-- 筛选栏 -->
        <div class="filter-bar">
          <a-select 
            v-model:value="storeFilters.type" 
            placeholder="题目类型" 
            style="width: 120px; margin-right: 10px"
            :options="[
              { value: '', label: '全部类型' },
              { value: '单选题', label: '单选题' },
              { value: '多选题', label: '多选题' },
              { value: '判断题', label: '判断题' },
              { value: '简答题', label: '简答题' }
            ]"
          />
          <a-select
            v-model:value="storeFilters.difficulty"
            placeholder="难度级别"
            style="width: 120px; margin-right: 10px"
            :options="[
              { value: '', label: '全部难度' },
              { value: '简单', label: '简单' },
              { value: '中等', label: '中等' },
              { value: '困难', label: '困难' }
            ]"
          />
          <a-select
            v-model:value="storeFilters.analysis"
            placeholder="答案解析"
            style="width: 120px; margin-right: 10px"
            :options="[
              { value: '', label: '有无解析' },
              { value: 'yes', label: '有解析' },
              { value: 'no', label: '无解析' }
            ]"
          />
          <a-input-search
            v-model:value="storeFilters.keyword"
            placeholder="搜索题目"
            style="width: 200px"
            @search="handleStoreSearch"
          />
        </div>

        <!-- 题目表格 -->
        <div class="question-table">
          <a-table
            :columns="storeColumns"
            :data-source="filteredStoreQuestions"
            row-key="id"
            size="middle"
            bordered
            :pagination="{ pageSize: 10 }"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'type'">
                <a-tag :color="getTypeColor(record.type)">
                  {{ record.type }}
                </a-tag>
              </template>
              <template v-else-if="column.key === 'question'">
                <div class="question-content">
                  <div class="question-text">{{ record.question }}</div>
                  <div v-if="record.options" class="question-options">
                    <div v-for="(option, key) in record.options" :key="key" class="option-item">
                      <span class="option-key">{{ key }}.</span> {{ option }}
                    </div>
                  </div>
                </div>
              </template>
              <template v-else-if="column.key === 'answer'">
                <div class="question-answer">
                  {{ Array.isArray(record.answer) ? record.answer.join(', ') : record.answer }}
                </div>
              </template>
              <template v-else-if="column.key === 'analysis'">
                <a v-if="record.analysis" style="color: #1890ff; cursor: pointer" @click="showAnalysisModal(record)" >
                  查看解析
                </a>
                <span v-else  style="color: #999; cursor: not-allowed" >
                  无解析
                </span>
              </template>
            </template>
          </a-table>
        </div>
      </div>

      <!-- 考试测试模块 -->
      <div class="content" v-if="state.windowWidth <= 520 || state.section === 'examination'">
        <div class="content-header">
          <h3>考试测试</h3>
          <div class="results-actions">
            <a-button type="primary" @click="showCreateExamModal">
              新建试卷
            </a-button>
          </div>
        </div>

        <template v-if="currentExam">
          <ExamInterfaceComponent 
            :exam="currentExam"
            @submit="handleExamSubmit"
          />
        </template>
        <template v-else>
          <a-table
            :columns="examColumns"
            :data-source="examList"
            row-key="id"
            size="small"
            bordered
            :pagination="{ pageSize: 10 }"
          >
            <template #bodyCell="{ column, text, record }">
              <!-- 提交状态列 -->
              <template v-if="column.key === 'submission_content'">
                <span v-if="text">
                  <CheckCircleFilled style="color: #41A317; margin-right: 4px;" />
                  已提交
                </span>
                <span v-else>
                  <ClockCircleFilled style="color: #FFCD43; margin-right: 4px;" />
                  待测试
                </span>
              </template>

              <!-- 操作列 -->
              <template v-else-if="column.key === 'action'">
                <template v-if="record.submission_content">
                  <!-- 已提交：显示查看详情 -->
                  <a-button type="link" @click="showExamResult(record)">
                    考试详情
                  </a-button>
                  <a-button type="link" danger @click="deleteExam(record.id)">
                    删除记录
                  </a-button>
                </template>
                <template v-else>
                  <!-- 未提交：显示开始考试 + 删除 -->
                  <a-button type="link" @click="startExam(record)" :disabled="isExaming">
                    开始考试
                  </a-button>
                  <a-button type="link" danger @click="deleteExam(record.id)">
                    删除试卷
                  </a-button>
                </template>
              </template>

            </template>
          </a-table>
        </template>

        <a-modal
          v-model:open="examModalVisible"
          title="新建试卷"
          width="800px"
          :footer="null"
          style="top: 10vh"
        >
          <a-form
            :model="examForm"
            :label-col="{ span: 6 }"
            :wrapper-col="{ span: 16 }"
          >
            <a-form-item label="试卷标题">
              <a-input v-model:value="examForm.title" />
            </a-form-item>
            <a-form-item label="考试时间(分钟)">
              <a-input-number v-model:value="examForm.duration" :min="10" :max="180" />
            </a-form-item>
            <a-form-item label="题目筛选">
              <a-tabs>
                <a-tab-pane key="1" tab="按题型">
                  <div class="question-type-selector">
                    <div class="type-item">
                      <span>单选题: (总数: {{ allStoreQuestions.filter(q => q.type === '单选题').length }})</span>
                      <a-input-number 
                        v-model:value="examForm.singleChoiceCount" 
                        :min="0" 
                        :max="allStoreQuestions.filter(q => q.type === '单选题').length"
                        placeholder="抽题数量"
                      />
                    </div>
                    <div class="type-item">
                      <span>多选题: (总数: {{ allStoreQuestions.filter(q => q.type === '多选题').length }})</span>
                      <a-input-number 
                        v-model:value="examForm.multiChoiceCount" 
                        :min="0" 
                        :max="allStoreQuestions.filter(q => q.type === '多选题').length"
                        placeholder="抽题数量"
                      />
                    </div>
                    <div class="type-item">
                      <span>判断题: (总数: {{ allStoreQuestions.filter(q => q.type === '判断题').length }})</span>
                      <a-input-number 
                        v-model:value="examForm.trueFalseCount" 
                        :min="0" 
                        :max="allStoreQuestions.filter(q => q.type === '判断题').length"
                        placeholder="抽题数量"
                      />
                    </div>
                    <div class="type-item">
                      <span>简答题: (总数: {{ allStoreQuestions.filter(q => q.type === '简答题').length }})</span>
                      <a-input-number 
                        v-model:value="examForm.shortAnswerCount" 
                        :min="0" 
                        :max="allStoreQuestions.filter(q => q.type === '简答题').length"
                        placeholder="抽题数量"
                      />
                    </div>
                  </div>
                </a-tab-pane>
                <a-tab-pane key="2" tab="按难度">
                  <div class="difficulty-selector">
                    <div class="difficulty-item">
                      <span>简单:</span>
                      <a-input-number v-model:value="examForm.easyCount" :min="0" :max="50" placeholder="抽题数量"/>
                    </div>
                    <div class="difficulty-item">
                      <span>中等:</span>
                      <a-input-number v-model:value="examForm.mediumCount" :min="0" :max="50" placeholder="抽题数量"/>
                    </div>
                    <div class="difficulty-item">
                      <span>困难:</span>
                      <a-input-number v-model:value="examForm.hardCount" :min="0" :max="50" placeholder="抽题数量"/>
                    </div>
                  </div>
                </a-tab-pane>
                <a-tab-pane key="3" tab="随机抽题">
                  <a-input-number v-model:value="examForm.randomCount" :min="0" :max="100" placeholder="抽题数量"/>
                </a-tab-pane>
              </a-tabs>
            </a-form-item>
            <a-form-item :wrapper-col="{ offset: 6, span: 16 }">
              <a-button type="primary" @click="createExam">
                创建试卷
              </a-button>
            </a-form-item>
          </a-form>
        </a-modal>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, h ,computed} from 'vue'
import { StarOutlined,CheckCircleFilled,ClockCircleFilled,FormOutlined,OrderedListOutlined, BookOutlined, FlagOutlined,DatabaseOutlined,RedditOutlined,FundProjectionScreenOutlined,CheckCircleOutlined} from '@ant-design/icons-vue'
import { Modal } from 'ant-design-vue'
import ExamInterfaceComponent from '@/components/ExamInterfaceComponent.vue'
import HeaderComponent from '@/components/HeaderComponent.vue'
import ItemDataBaseInfoView from './ItemDataBaseInfoView.vue'
import { BookCheck } from 'lucide-vue-next'
import { message } from 'ant-design-vue'
import { knowledgeBaseApi } from '@/apis/admin_api'
import { useConfigStore } from '@/stores/config'
import { chatApi } from '@/apis/auth_api'
import { MdPreview } from 'md-editor-v3'
import { itemRecordApi,examRecordApi} from '@/apis/auth_api'
import dayjs from 'dayjs';

const state = reactive({
  section: 'intro',
  windowWidth: window?.innerWidth || 0,
  loading: false
})

const features = [
  {
    icon: BookOutlined,
    title: '题源知识库',
    desc: '整合丰富的党建知识资源，构建完整的知识体系'
  },
  {
    icon: FormOutlined,
    title: '智能题目生成',
    desc: 'AI自动生成各类党建题目，支持单选、多选、判断等题型'
  },
  {
    icon: DatabaseOutlined,
    title: '历史题库管理',
    desc: '保存和管理生成的题目，构建个性化题库'
  },
  {
    icon: FundProjectionScreenOutlined,
    title: '考试测试',
    desc: '模拟考试环境，检验学习成果'
  }
]

const highlights = [
  '支持多种题型和难度级别设置',
  '答案解析帮助深入理解知识点',
  '界面简洁友好，操作便捷',
  '持续更新党建知识内容'
]

const questionForm = reactive({
  type: '单选题',
  count: 3,
  difficulty: '中等',
  analysis: 'yes',
  optionsNum: '4',
  // keywords: '要坚持党性党⻛党纪⼀起抓，把《条例》纳⼊党 员、⼲部培训必修课，增强遵规守纪的⾃觉。要坚持把纪律挺在前⾯，促进执纪执法贯通，准确运⽤"四种形 态"，落实"三个区分开来"，把从严管理监督和⿎励担当作为⾼度统⼀起来。',
  keywords: '',
  modelName: '智能文档助手'
})

const isGenerating = ref(false)
const questionResults = ref('')
const isSaving = ref(false)
const isRefreshing = ref(false)
const historyQuestions = ref([])
const detailModalVisible = ref(false)
const selectedQuestion = ref(null)
const isIndexing = ref(false)

const historyColumns = [
  { title: '创建时间', dataIndex: 'createdAt', key: 'createdAt', width: 120 },
  { title: '题目类型', dataIndex: 'type', key: 'type', width: 80 },
  { title: '题目数量', dataIndex: 'count', key: 'count', width: 80 },
  { title: '难度级别', dataIndex: 'difficulty', key: 'difficulty', width: 80 },
  { title: '答案解析', dataIndex: 'analysis', key: 'analysis', width: 80 },
  { title: '生成模型', dataIndex: 'modelName', key: 'modelName', width: 150 },
  { title: '操作', key: 'action', width: 180 }
]

const storeColumns = [
  { title: '题目类型', dataIndex: 'type', key: 'type' },
  { title: '题目内容', dataIndex: 'question', key: 'question',width:800},
  { title: '正确答案', dataIndex: 'answer', key: 'answer' },
  { title: '难度', dataIndex: 'difficulty', key: 'difficulty' },
  { title: '解析', dataIndex: 'analysis', key: 'analysis'}
]

const storeFilters = reactive({
  type: '',
  difficulty: '',
  keyword: '',
  analysis: ''
})

const modelOptions = ref([
  { value: '山海党建管理大模型', label: '山海党建管理大模型' },
])

const opts = reactive({
  databases: []
})

const meta = reactive({
  selectedKB: null
})

const targetDatabaseName = '学堂在线-党建智能题目生成'
const targetDatabase = reactive({})
const loadingDatabase = ref(false)

const clearResults = () => {
  questionResults.value = ''
  message.info('已清空当前题目')
}

const getTypeColor = (type) => {
  const colors = {
    '单选题': 'blue',
    '多选题': 'green',
    '判断题': 'orange',
    '简答题': 'purple'
  }
  return colors[type] || 'gray'
}


const item_index_template = `
你是一个专业的中文试题解析助手。你的任务是将给定的非结构化文本题目，提取成标准的结构化 JSON 数据。要求如下:

1. 支持的题型:
   - 单选题(single choice)
   - 多选题(multiple choice)
   - 判断题(true/false)
   - 简答题(short answer)

2. 输出格式必须为 JSON 数组，每个题目为一个对象:
{
  "id": 题号(从1开始的整数),
  "type": "单选题" | "多选题" | "判断题" | "简答题",
  "question": "题干文本",
  "options": {           // 单选和多选题才有
    "A": "选项A内容",
    "B": "选项B内容",
    "C": "选项C内容",
    "D": "选项D内容"
  },
  "answer": "正确答案，单选填一个选项，多选用数组，如['A','C']，判断题填“对”或“错”，简答题填简短答案文本",
  "analysis": "解析文本，可选"
}

3. 解析规则:
   - 题干:紧跟题号之后的文字，直到出现选项或答案。
   - 选项:以 A.、B.、C.、D. 开头的内容。
   - 答案:文本中标注的正确答案。
   - 解析:若题目有解析，则提取；没有则留空。

4. 输出严格为 JSON，不允许包含其他说明或多余文字。

5. 输入输出示例:
题目文本:
  各级党委(党组)要担负起全面从严治党的责任，包括以下哪些内容？
  A. 认真抓好《条例》的贯彻执行
  B. 仅惩治轻微的违纪行为
  C. 忽视纪律的刚性、严肃性
  D. 不纳入党员、干部培训必修课
  答案:A
  解析:各级党委(党组)要全面从严治党，因此选A。
  通知，党委（党组）要担负起全面从严治党政治责任，认真抓好《条例》的贯彻执行。
  答案：对
  解析：根据通知要求，各级党委（党组）确实需要担负起全面从严治党政治责任，认真抓好《条例》的贯彻执行。

你的输出:
[
  {
    "id": 1,
    "type": "单选题",
    "question": "各级党委(党组)要担负起全面从严治党的责任，包括以下哪些内容？",
    "options": {
      "A": "认真抓好《条例》的贯彻执行",
      "B": "仅惩治轻微的违纪行为",
      "C": "忽视纪律的刚性、严肃性",
      "D": "不纳入党员、干部培训必修课"
    },
    "answer": "A",
    "analysis": "各级党委(党组)要全面从严治党，因此选A。"
  },
  {
    "id": 2,
    "type": "判断题",
    "question": "通知，党委（党组）要担负起全面从严治党政治责任，认真抓好《条例》的贯彻执行。",
    "answer": "对",
    "analysis": "根据通知要求，各级党委（党组）确实需要担负起全面从严治党政治责任，认真抓好《条例》的贯彻执行。"
  }
]

现在，请将下面的非结构化文本题目提取成 JSON 格式:
`

const knowbase_itemGen_template = `
你是一名专业的中文出题助手。你的任务是根据提供的已知信息，严格按照要求生成指定数量的题目，并提供正确答案。要求如下:
1. **匹配要求**
- 题目类型、题目数量、难度、选项数(若适用)必须与用户要求一致。
- 不得虚构无关内容，不得添加无关符号。
2. **题型规范**
- 选择题:生成指定数量的选择题，按给定选项数量出题。
- 判断题:生成判断对错题，正确答案仅为“对”或“错”，不得附加多余文字。
- 简答题:使用中文出题，并给出简短精炼的中文答案。
- 若用户要求提供解析，则在正确答案后附加简要解析。
3. **题目主题**
- 生成的题目应与党政建设主题相关，且符合题型规范
4. **输出格式**
- 题干、选项、答案之间需严格换行分隔。
- 选项按 A.、B.、C.、D. 排列(若适用)。
- 不要在输出中包含本提示词内容。
<已知用户输入参数>{params}</已知用户输入参数>
请生成符合以上要求的题目。`

const showQuestionDetail = (question) => {
  Modal.info({
    title: `题目详情 (${question?.type || ''})`,
    width: '60%',
    style: { top: '2vh'},
    okText: '关闭',
    content: h(MdPreview, {
      modelValue: question?.content || ''
    })
  })
}

const showAnalysisModal = (question) => {
  Modal.info({
    title: '题目解析',
    content: question.analysis || '暂无解析',
    okText: '知道了',
  })
}

const delItemRecord = (itemId) => {
  Modal.confirm({
    title: '删除题目',
    content: '确定要删除这条题目记录吗？',
    okText: '确认',
    cancelText: '取消',
    onOk: async() => {
        try {
          await itemRecordApi.deleteItemRecord(itemId)
          message.success('删除题目记录成功');
          await loadItemRecords();
        } catch (e) {
          message.error('删除题目记录失败', `${e.message || '未知错误'}`);
        }
      }
  })
}

const refreshHistory = async () => {
  isRefreshing.value = true
  await loadItemRecords()
  await loadExamRecords()
  isRefreshing.value = false
  message.success('刷新完成')
}


const generateUniqueHash = () => {
  const timestamp = Date.now().toString(36); 
  const randomStr = Math.random().toString(36).slice(2, 8);
  return timestamp + randomStr;
}

const saveToHistory = async () => {
  isSaving.value = true
  try {
    const newItem = {
      id: generateUniqueHash(),
      content: questionResults.value,
      createdAt: new Date().toLocaleString(),
      type: questionForm.type,
      count: questionForm.count,
      difficulty: questionForm.difficulty,
      analysis: questionForm.analysis === 'yes' ? '有' : '无',
      modelName: questionForm.modelName,
      structured_content: '', // 结构化题目暂时不处理
      params: { ...questionForm }
    }
    await saveItemRecords(newItem)
    message.success('题目已保存至历史题库')
    state.section = 'questions'
  } catch (error) {
    console.error('保存失败:', error)
    message.error('保存失败，请重试')
  } finally {
    isSaving.value = false
  }
}

const itemParams = computed(() => `
  - 题目类型:${questionForm.type}
  - 选择题选项数量:${questionForm.optionsNum}
  - 是否有答案解析:${questionForm.analysis}
  - 题目数量:${questionForm.count}
  - 题目难度:${questionForm.difficulty}
  - 生成题目的主题关键词、句:${questionForm.keywords}
`)

const generateQuestions = async () => {
  isGenerating.value = true;
  questionResults.value = '';
  state.section='results';
  let query;
  let db_id;

  if (questionForm.keywords.trim() === '') {
      query = knowbase_itemGen_template.replace('{params}', itemParams.value)
      db_id = null
  }else {
      query = questionForm.keywords
      db_id = meta.selectedKB
  }
  try {
    const controller = new AbortController();
    const signal = controller.signal;
    const response = await chatApi.sendMessageWithAbort({
        query: query,
        meta: {
          db_id: db_id,
          use_graph: false,
          use_web: false,
          isItemRequest: itemParams.value
        }
    },signal);

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let lastUpdateTime = 0;

    const readChunk = () => {
      return reader.read().then(({ done, value }) => {
        if (done) {
          isGenerating.value = false;
          return;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        const now = Date.now();

        for (let i = 0; i < lines.length - 1; i++) {
          const line = lines[i].trim();
          if (!line) continue;

          try {
            const data = JSON.parse(line);
            if (typeof data.response === 'string' && data.response.length > 0) {
              if (now - lastUpdateTime > 100 || i === lines.length - 2) {
                questionResults.value += data.response;
                lastUpdateTime = now;
              }
            }
          } catch (e) {
            console.error('JSON解析错误:', e);
          }
        }

        buffer = lines[lines.length - 1];
        return readChunk();
      });
    };

    await readChunk();
  } catch (error) {
    console.error('生成题目失败:', error);
    message.error('题目生成失败，请重试');
    isGenerating.value = false;
    questionResults.value = '题目生成失败，请稍后重试';
  }
};

const findOrCreateDatabase = async () => {
  loadingDatabase.value = true;
  try {
    const { databases } = await knowledgeBaseApi.getDatabases();
    opts.databases = databases;
    const dbInfo = databases.find(db => db.name === targetDatabaseName);
    
    if (dbInfo) {
      Object.assign(targetDatabase, dbInfo);
      meta.selectedKB = dbInfo.db_id;
      
    } else {
      const newDb = await knowledgeBaseApi.createDatabase({
        database_name: targetDatabaseName,
        description: '智能生成题目的知识库'
      });
      Object.assign(targetDatabase, newDb);
      meta.selectedKB = newDb.db_id;
      message.success(`已创建知识库: ${targetDatabaseName}`);
    }
  } catch (error) {
    console.error('处理知识库出错:', error);
    message.error(error.message || '处理知识库失败');
  } finally {
    loadingDatabase.value = false;
  }
};

const isValidJson = (str) => {
  if (typeof str !== 'string') return false;
  try {
    const obj = JSON.parse(str);
    return obj !== null && (typeof obj === 'object' || Array.isArray(obj));
  } catch {
    return false;
  }
}

const indexItem = async (itemRecord) => {
  const indexItemPrompt = item_index_template + "<题目文本> " + itemRecord.content + " </题目文本>"
  try {
    const data = await chatApi.simpleCall(indexItemPrompt);
    if (isValidJson(data.response)){
      itemRecord.structured_content = data.response;
      console.info('索引题目数据:', itemRecord);
      await saveItemRecords(itemRecord);
      message.success('题目已成功建立索引');
    }
    else {
      message.error('索引题目失败，大模型返回数据格式不正确，请重试或切换模型');
    }
  } catch (error) {
    console.error('索引题目失败:', error);
    message.error('索引题目失败，请稍后重试',error);
  }
}

const handleIndexClick = async (record) => {
  if (record.structured_content) {
    try {
      const content = JSON.parse(record.structured_content);
      Modal.info({
        title: '索引内容',
        width: '80%',
        style: { top: '5vh', padding: '20px' },
        content: h('div', { style: 'max-height: 80vh; overflow: auto;' }, [
          h('table', { style: 'width: 100%; border-collapse: collapse;' }, [
            h('thead', [
              h('tr', [
                h('th', { style: 'padding: 8px; border: 1px solid #ddd; background: #f5f5f5;' }, '题号'),
                h('th', { style: 'padding: 8px; border: 1px solid #ddd; background: #f5f5f5;' }, '题型'),
                h('th', { style: 'padding: 8px; border: 1px solid #ddd; background: #f5f5f5;' }, '题干'),
                h('th', { style: 'padding: 8px; border: 1px solid #ddd; background: #f5f5f5;' }, '选项'),
                h('th', { style: 'padding: 8px; border: 1px solid #ddd; background: #f5f5f5;' }, '答案'),
                h('th', { style: 'padding: 8px; border: 1px solid #ddd; background: #f5f5f5;' }, '解析')
              ])
            ]),
            h('tbody', 
              content.map(item => h('tr', [
                h('td', { style: 'padding: 8px; border: 1px solid #ddd;' }, item.id),
                h('td', { style: 'padding: 8px; border: 1px solid #ddd;' }, item.type),
                h('td', { style: 'padding: 8px; border: 1px solid #ddd;' }, item.question),
                h('td', { style: 'padding: 8px; border: 1px solid #ddd;' }, 
                  item.options ? Object.entries(item.options).map(([key, val]) => 
                    h('div', `${key}. ${val}`)
                  ) : '-'
                ),
                h('td', { style: 'padding: 8px; border: 1px solid #ddd;' }, 
                  Array.isArray(item.answer) ? item.answer.join(', ') : item.answer
                ),
                h('td', { style: 'padding: 8px; border: 1px solid #ddd;' }, item.analysis || '-')
              ]))
            )
          ])
        ]),
        okText: '关闭'
      });
    } catch (e) {
      Modal.info({
        title: '索引内容',
        width: '60%',
        content: h('pre', record.structured_content),
        okText: '关闭'
      });
    }
  } else {
      isIndexing.value = true;
      await indexItem(record);
      isIndexing.value = false;
  }
}

const handleChange = (selectedDbId) => {
  meta.selectedKB = selectedDbId;
}

const updateWindowWidth = () => {
  state.windowWidth = window?.innerWidth || 0
}

// 加载题目生成记录
const loadItemRecords = async () => {
  try {
    const res = await itemRecordApi.getItemRecords()
    if (res.length !== 0) {
      const updated = res.map(record => ({
        ...record.content,
        createdAt: dayjs(record.createdtime).format('YYYY-MM-DD HH:mm:ss')
      }));
      historyQuestions.value = updated
      // message.success('获取题库成功');
    }else{
      historyQuestions.value = []
    }
  } catch (e) {
    message.error(`加载题库失败:${e.message || '未知错误'}`);
  }
}

const allStoreQuestions = computed(() => {
  const questions = []
  historyQuestions.value.forEach(item => {
    try {
      if (item.structured_content) {
        const content = JSON.parse(item.structured_content)
        if (Array.isArray(content)) {
          content.forEach(q => {
            questions.push({
              ...q,
              difficulty: item.difficulty,
              createdAt: item.createdAt
            })
          })
        }
      }
    } catch (e) {
      console.error('解析题目失败:', e)
    }
  })
  return questions
})

const filteredStoreQuestions = computed(() => {
  return allStoreQuestions.value.filter(q => {
    const typeMatch = !storeFilters.type || q.type === storeFilters.type
    const difficultyMatch = !storeFilters.difficulty || q.difficulty === storeFilters.difficulty
    const keywordMatch = !storeFilters.keyword || 
      q.question.includes(storeFilters.keyword) ||
      (q.options && Object.values(q.options).some(opt => opt.includes(storeFilters.keyword)))
    const analysisMatch = !storeFilters.analysis || 
      (storeFilters.analysis === 'yes' && q.analysis) ||
      (storeFilters.analysis === 'no' && !q.analysis)
    return typeMatch && difficultyMatch && keywordMatch && analysisMatch
  })
})

const handleStoreSearch = () => {
  // 搜索逻辑已集成在computed属性中
}

// 保存题目记录
const saveItemRecords = async (currItemRecord) => {
  try {
    await itemRecordApi.saveItemRecords(currItemRecord)
    await loadItemRecords()
    message.success('保存题目记录成功')
  } catch (e) {
    message.error('保存题目记录失败')
  }
}

const showPromptTemplate = () => {
  Modal.info({
    title: '题目生成提示词模板',
    width: '60%',
    content: h(MdPreview, { modelValue: knowbase_itemGen_template }),
    okText: '关闭',
    maskClosable: true,
    style: { top: '20px'}
  })
}

// 考试相关状态和方法
const examColumns = [
  { title: '试卷标题', dataIndex: 'title', key: 'title' },
  { title: '题目数量', dataIndex: 'questionCount', key: 'questionCount' },
  { title: '考试时间(分钟)', dataIndex: 'duration', key: 'duration' },
  { title: '创建时间', dataIndex: 'createdAt', key: 'createdAt' },
  { title: '状态', dataIndex: 'submission_content',key: 'submission_content'},
  { title: '操作', key: 'action', width: 300}
]

const questionTypeCounts = computed(() => {
  const counts = {
    '单选题': 0,
    '多选题': 0,
    '判断题': 0,
    '简答题': 0,
    '简单': 0,
    '中等': 0,
    '困难': 0
  }
  
  allStoreQuestions.value.forEach(q => {
    counts[q.type]++
    counts[q.difficulty]++
  })
  
  return counts
})

const examList = ref([])
const examModalVisible = ref(false)
const isExaming = ref(false)
const examForm = reactive({
  title: '',
  duration: 60,
  singleChoiceCount: null,
  multiChoiceCount: null,
  trueFalseCount: null,
  shortAnswerCount: null,
  easyCount: null,
  mediumCount: null,
  hardCount: null,
  randomCount: null
})

const showCreateExamModal = () => {
  examForm.title = '试卷' + generateUniqueHash()
  examModalVisible.value = true
}

const loadExamRecords = async () => {
  try {
    const res = await examRecordApi.getExamRecords()
    if (res.length !== 0) {
      const updated = res.map(record => ({
        ...record.content,
        submission_content:record.submission_content,
        createdAt: dayjs(record.createdtime).format('YYYY-MM-DD HH:mm:ss')
      }));
      examList.value = updated
    }else{
      examList.value = []
    }
  } catch (error) {
    console.error('获取试卷记录失败:', error)
    message.error('获取试卷记录失败，请重试')
  }
}

const createExam = async () => {
  try {
    // 验证试卷标题
    if (!examForm.title.trim()) {
      message.error('请输入试卷标题')
      return
    }

    // 获取题库中各类型题目数量
    const typeCounts = {
      '单选题': allStoreQuestions.value.filter(q => q.type === '单选题').length,
      '多选题': allStoreQuestions.value.filter(q => q.type === '多选题').length,
      '判断题': allStoreQuestions.value.filter(q => q.type === '判断题').length,
      '简答题': allStoreQuestions.value.filter(q => q.type === '简答题').length
    }

    // 验证各类型题目数量
    if (examForm.singleChoiceCount > typeCounts['单选题']) {
      message.error(`单选题数量超过题库限制(最大${typeCounts['单选题']})`)
      return
    }
    if (examForm.multiChoiceCount > typeCounts['多选题']) {
      message.error(`多选题数量超过题库限制(最大${typeCounts['多选题']})`)
      return
    }
    if (examForm.trueFalseCount > typeCounts['判断题']) {
      message.error(`判断题数量超过题库限制(最大${typeCounts['判断题']})`)
      return
    }
    if (examForm.shortAnswerCount > typeCounts['简答题']) {
      message.error(`简答题数量超过题库限制(最大${typeCounts['简答题']})`)
      return
    }

    // 筛选题目
    const questions = []
    const allQuestions = allStoreQuestions.value
    
    // 按题型筛选
    if (examForm.singleChoiceCount > 0) {
      const singleChoice = allQuestions
        .filter(q => q.type === '单选题')
        .slice(0, examForm.singleChoiceCount)
      questions.push(...singleChoice)
    }
    
    if (examForm.multiChoiceCount > 0) {
      const multiChoice = allQuestions
        .filter(q => q.type === '多选题')
        .slice(0, examForm.multiChoiceCount)
      questions.push(...multiChoice)
    }
    
    if (examForm.trueFalseCount > 0) {
      const trueFalse = allQuestions
        .filter(q => q.type === '判断题')
        .slice(0, examForm.trueFalseCount)
      questions.push(...trueFalse)
    }
    
    if (examForm.shortAnswerCount > 0) {
      const shortAnswer = allQuestions
        .filter(q => q.type === '简答题')
        .slice(0, examForm.shortAnswerCount)
      questions.push(...shortAnswer)
    }
    
    // 按难度筛选
    if (examForm.easyCount > 0) {
      const easy = allQuestions
        .filter(q => q.difficulty === '简单')
        .slice(0, examForm.easyCount)
      questions.push(...easy)
    }
    
    if (examForm.mediumCount > 0) {
      const medium = allQuestions
        .filter(q => q.difficulty === '中等')
        .slice(0, examForm.mediumCount)
      questions.push(...medium)
    }
    
    if (examForm.hardCount > 0) {
      const hard = allQuestions
        .filter(q => q.difficulty === '困难')
        .slice(0, examForm.hardCount)
      questions.push(...hard)
    }
    
    // 随机抽题
    if (examForm.randomCount > 0) {
      const availableCount = allQuestions.length - questions.length
      if (examForm.randomCount > availableCount) {
        message.error(`随机抽题数量超过剩余题目数量(最大${availableCount})`)
        return
      }
      const randomQuestions = [...allQuestions]
        .filter(q => !questions.includes(q))
        .sort(() => 0.5 - Math.random())
        .slice(0, examForm.randomCount)
      questions.push(...randomQuestions)
    }
    
    // 去重
    const uniqueQuestions = Array.from(new Set(questions.map(q => q.question)))
      .map(question => questions.find(q => q.question === question))
    
    // 创建试卷
    const newExam = {
      id: generateUniqueHash(),
      title: examForm.title,
      duration: examForm.duration,
      questions: uniqueQuestions,
      questionCount: uniqueQuestions.length,
      createdAt: new Date().toLocaleString()
    }
    
    try {
      await examRecordApi.saveExamRecord(newExam)
      await loadExamRecords()
      message.success('试卷保存成功')
      examModalVisible.value = false
    } catch (error) {
      console.error('保存试卷失败:', error)
      message.error('保存试卷失败，请重试')
    }
  } catch (error) {
    console.error('创建试卷失败:', error)
    message.error('创建试卷失败，请重试')
  }
}

const currentExam = ref(null)

const startExam = (exam) => {
  isExaming.value = true
  const examUrl = `${window.location.origin}/exam/${exam.id}`
  window.open(examUrl, '_blank')
  message.info(`考试已在新窗口打开: ${exam.title}`)
}

const handleExamSubmit = async (result) => {
  console.info("result",result)
  const submission = {
      id: result.examId,
      score: result.score,
      answers: result.answers,
      createdAt: new Date().toLocaleString()
  }
  try {
    await examRecordApi.saveExamSubmissionRecord(submission)
    await loadExamRecords()
    message.success(`考试提交成功，得分: ${result.score}`)
    console.error('考试提交成功，得分:',result.score)
    isExaming.value = false
    currentExam.value = null
  } catch (error) {
    console.error('保存考试结果失败:', error)
    message.error('保存考试结果失败，请重试')
  }
}

const deleteExam = (id) => {
  Modal.confirm({
    title: '删除试卷',
    content: '确定要删除这份试卷吗？',
    okText: '确认',
    cancelText: '取消',
    onOk: async() => {
      try {
        console.info('试卷已删除',id)
        await examRecordApi.deleteExamRecord(id)
        await loadExamRecords()
      } catch (error) {
        message.error('删除失败，请稍后重试')
      }
    }
  })
}

const showExamResult = (record) => {
  Modal.info({
    title: `考试结果 - ${record.title}`,
    width: '60%',
    style: { top: '2vh'},
    content: h('div', { style: 'max-height: 80vh; overflow: auto;margin-top: 20px' }, [
      // h('h3', { style: 'margin-bottom: 16px;' }, '你的答案与正确答案对比'),
      ...record.questions.map((q, index) => {
        const userAnswer = record.submission_content?.answers?.[index]
        return h('div', { 
          style: 'margin-bottom: 24px; border-bottom: 1px dashed #eee; padding-bottom: 16px;' 
        }, [
          h('div', { style: 'font-weight: bold; margin-bottom: 8px;' }, `${index + 1}. ${q.question}`),
          q.options && h('div', { style: 'margin-bottom: 8px;' }, [
            h('div', { style: 'color: #666; margin-bottom: 4px;' }, '选项:'),
            ...Object.entries(q.options).map(([key, val]) => 
              h('div', { 
                style: `margin-left: 16px; color: ${q.answer.includes(key) ? '#52c41a' : '#666'};` 
              }, `${key}. ${val}`)
            )
          ]),
          h('div', { style: 'margin-bottom: 8px;' }, [
            h('span', { style: 'color: #666;' }, '你的答案: '),
            h('span', { 
              style: `color: ${Array.isArray(userAnswer) 
                ? userAnswer.every(a => q.answer.includes(a)) && userAnswer.length === q.answer.length 
                  ? '#52c41a' : '#f5222d'
                : userAnswer === q.answer ? '#52c41a' : '#f5222d'};`
            }, Array.isArray(userAnswer) ? userAnswer.join(', ') : userAnswer)
          ]),
          h('div', { style: 'margin-bottom: 8px;' }, [
            h('span', { style: 'color: #666;' }, '正确答案: '),
            h('span', { style: 'color: #52c41a;' }, Array.isArray(q.answer) ? q.answer.join(', ') : q.answer)
          ]),
          q.analysis && h('div', [
            h('div', { style: 'color: #666; margin-bottom: 4px;' }, '解析:'),
            h('div', { style: 'margin-left: 16px; color: #666;' }, q.analysis)
          ])
        ])
      })
    ]),
    okText: '关闭'
  })
}


const handleExamResult = (event) => {
  if (event.data.type === 'examSubmitted') {
    try {
      const result = JSON.parse(event.data.data) // 反序列化数据
      console.log('收到考试结果:', result)
      handleExamSubmit(result)
    } catch (e) {
      console.error('解析考试结果失败:', e)
    }
  } 
  if (event.data.type === 'examClosed') {
    isExaming.value = false;
    message.warning('考试页面已关闭');
  }
}

onMounted(() => {
  updateWindowWidth()
  window.addEventListener('resize', updateWindowWidth)
  findOrCreateDatabase()
  loadItemRecords()
  loadExamRecords()
  window.addEventListener('message', handleExamResult)
})

</script>

<style lang="less" scoped>
.item-container {
  --header-height: 8%;
  height: 100vh;
}

.item-header {
  height: var(--header-height);
}

.item-content {
  display: flex;
  position: relative;
  height: 100%;
  overflow: hidden;
}

.sider {
  width: 18%;
  padding: 0 20px;
  position: fixed;
  top: var(--header-height);
  left: 0;
  height: calc(100vh - var(--header-height));
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  border-right: 1px solid var(--main-light-3);
  gap: 15px;
  padding-top: 20px;

  & > * {
    width: 100%;
    height: auto;
    padding: 6px 16px;
    cursor: pointer;
    transition: all 0.1s;
    text-align: left;
    font-size: 16px;
    border-radius: 8px;
    color: var(--gray-700);

    &:hover {
      background: var(--gray-100);
      color: brown;
    }

    &.activesec {
      background: var(--gray-200);
      color: brown;
    }
  }

  .sider-title {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 12px 0;
    margin-bottom: 15px;
    background: linear-gradient(to right, #f8e7e7, #f0d2d2);
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
    color: #8b1313;
    font-weight: bold;
    font-size: 1.2rem;
    border-bottom: none;
    
    h2 {
      margin: 0;
      font-size: 1.1em;
    }
  }
}

  .content {
  .filter-bar {
    display: flex;
    margin-bottom: 16px;
    padding: 0 24px;
  }

  .question-table {
    padding: 0 24px;

    .question-content {
      .question-text {
        font-weight: 500;
        margin-bottom: 8px;
      }

      .question-options {
        .option-item {
          margin-bottom: 4px;
          
          .option-key {
            font-weight: bold;
            color: var(--main-600);
          }
        }
      }
    }

    .question-answer {
      font-weight: bold;
      color: var(--success-6);
    }
  }
    flex: 1;
    padding: 20px;
    margin-left: 18%;
    // height: 100%;
    background: white;
    border-radius: 8px;
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.03);
    border: 1px solid var(--main-light-3);
    overflow-y: auto;
    overflow-x: hidden;
    
    & > * {
      padding-right: 10px;
    }
    
    &.intro {
      padding: 30px;
      .intro-header {
        text-align: center;
        margin-bottom: 40px;
        padding:  20px;
        // background: linear-gradient(to right, rgba(255, 240, 240, 0.8), rgba(255, 255, 255, 0.9));
        border-radius: 12px;
        // border-left: 5px solid #8b1313;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        position: relative;
        overflow: hidden;
        
        &::before {
          content: "";
          position: absolute;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          // background: url('@/assets/logo.svg') no-repeat center;
          background-size: 20%;
          opacity: 0.05;
          z-index: 0;
        }
        
        h3 {
          font-size: 2rem;
          color: #8b1313;
          margin-bottom: 15px;
          position: relative;
          font-weight: 600;
          letter-spacing: 1px;
          text-shadow: 0 2px 4px rgba(139, 19, 19, 0.1);
        }
        
        .subtitle {
          font-size: 1.8rem;
          color: brown;
          position: relative;
          font-weight: 600;
          max-width: 80%;
          margin: 0 auto;
          line-height: 1.6;
          padding: 10px 0;
          border-top: 1px dashed rgba(139, 19, 19, 0.2);
          border-bottom: 1px dashed rgba(139, 19, 19, 0.2);
        }
      }
      
      .intro-features {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
        gap: 30px;
        margin-bottom: 20px;
        overflow: visible;

        .feature-card {
          padding: 25px;
          background: var(--main-light-8);
          border-radius: 8px;
          text-align: center;
          transition: all 0.3s;
          border: 1px solid var(--main-light-4);
          animation: fadeInUp 0.5s ease-out both;
          
          &:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 20px rgba(0, 0, 0, 0.1);
            background: linear-gradient(135deg, rgba(248, 231, 231, 0.8), rgba(255, 255, 255, 0.9));
            transition: all 0.5s cubic-bezier(0.25, 0.8, 0.25, 1);
          }
          
          .feature-icon {
            font-size: 2.5rem;
            color: #8b1313;
            margin-bottom: 15px;
            transition: all 0.3s;
          }
          
          h4 {
            font-size: 1.2rem;
            margin-bottom: 10px;
            color: var(--main-700);
            transition: all 0.3s;
          }
          
          p {
            color: var(--gray-600);
            line-height: 1.6;
            transition: all 0.3s;
          }
          
          &:hover .feature-icon,
          &:hover h4 {
            color: #b71c1c;
          }
          
          &:hover p {
            color: var(--gray-800);
          }
        }
      }
      
      .intro-highlights {
        background: var(--main-light-8);
        padding: 25px;
        border-radius: 8px;
        border: 1px solid var(--main-light-4);
        overflow: visible;
        
        h4 {
          font-size: 1.3rem;
          color: var(--main-700);
          margin-bottom: 20px;
          padding-bottom: 10px;
          border-bottom: 1px solid var(--main-light-3);
          display: flex;
          align-items: center;
          gap: 8px;
        }
        
        .highlight-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 15px;
          
          .highlight-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px;
            background: white;
            border-radius: 6px;
            transition: all 0.2s;
            justify-content: center;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            
            &:hover {
              transform: translateX(5px);
              box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            }
            
            .highlight-icon {
              color: #8b1313;
              font-size: 1.2rem;
            }
          }
        }
      }
      
      @keyframes fadeInUp {
        from {
          opacity: 0;
          transform: translateY(20px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }
    }

  h3 {
    margin-top: 0;
    margin-bottom: 20px;
    font-size: 1.3rem;
    color: var(--main-600);
  }

  .form-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 24px;
    margin-top: 16px;
    padding: 24px;
    background: white;
    border-radius: 8px;
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.03);
    border: 1px solid var(--main-light-3);
  }

  .form-item {
    padding: 16px;
    background: var(--main-light-8);
    border-radius: 6px;
    border: 1px solid var(--main-light-4);
    transition: all 0.2s;
    
    &:hover {
      border-color: var(--main-light-2);
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
    }

    label {
      display: block;
      margin-bottom: 12px;
      font-weight: 500;
      color: var(--main-600);
      font-size: 0.95rem;
    }

    :deep(.ant-select), 
    :deep(.ant-input-number),
    :deep(.ant-input-textarea) {
      width: 100% !important;
    }

    &.full-width {
      grid-column: 1 / -1;
      width: 100%;
    }
  }

  .question-results-container {
    min-height: 300px;
    position: relative;
    
    .question-results {
      padding: 16px;
      background: white;
      border-radius: 8px;
      border: 1px solid var(--main-light-3);
      box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.03);
    }

    .loading-prompt {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 200px;
    }

    .empty-prompt {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 200px;
      color: var(--gray-500);
    }
  }

  .empty-prompt {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 200px;
    color: var(--gray-500);
  }
  
  .content-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
    padding: 12px 24px;
    background: rgba(255, 255, 255, 0.329);
    border-radius: 8px;
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.03);
    border: 1px solid var(--main-light-3);
    
    h3 {
      margin: 0;
      font-size: 1.4rem;
      font-weight: 600;
      color: var(--main-700);
      display: flex;
      align-items: center;
      gap: 8px;
      
      &::before {
        content: '';
        display: block;
        width: 4px;
        height: 16px;
        background: var(--main-500);
        border-radius: 2px;
      }
    }

    .generate-button {
      height: 36px;
      padding: 0 20px;
      font-weight: 500;
      margin-right: 12px;
    }

    .prompt-button {
      height: 36px;
      padding: 0 20px;
    }

    .results-actions {
      gap: 12px;
      
      .save-button, .clear-button {
        height: 36px;
        padding: 0 16px;
      }
    }
  }

  .results-actions {
    display: flex;
    gap: 10px;
  }
}

.knowledge{
  padding: 0px;
}

.knowledge{
  padding: 0px;
}

.type-item{
  display: flex;
  justify-content: space-between;
  max-width: fit-content;
  align-items: center;
  padding: 12px;
  background: white;
  border-radius: 6px;
  border: 1px solid var(--main-light-3);
  transition: all 0.2s;
  margin-bottom: 12px;

  &:hover {
    border-color: var(--main-light-2);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
    background: var(--main-light-8);
  }

  span {
    font-weight: 500;
    color: brown;
    flex: 1;
  }

  :deep(.ant-input-number) {
    width: 120px;
    margin-left: 16px;
  }
}


.difficulty-item{
  display: flex;
  justify-content: space-between;
  max-width: fit-content;
  align-items: center;
  padding: 12px;
  background: white;
  border-radius: 6px;
  border: 1px solid var(--main-light-3);
  transition: all 0.2s;
  margin-bottom: 12px;

  &:hover {
    border-color: var(--main-light-2);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
    background: var(--main-light-8);
  }

  span {
    font-weight: 500;
    color: brown;
    flex: 1;
  }

  :deep(.ant-input-number) {
    width: 120px;
    margin-left: 16px;
  }
}

@media (max-width: 520px) {
  .item-content {
    flex-direction: column;
  }

  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
