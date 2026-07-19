<template>
  <div class="datamining-container">
    <!-- <HeaderComponent title="此页面为开发模板，并非真实使用场景" subtitle="学校办学数据查询与画像生成">
      <template #actions>
        <a-button type="primary" @click="exportData">
          <template #icon><ExportOutlined /></template>
          导出数据
        </a-button>
      </template>
    </HeaderComponent> -->

    <div class="dashboard">
      <!-- 数据查询区 -->
      <div class="query-section">
        <a-tabs v-model:activeKey="activeTab">
          <a-tab-pane key="college" tab="学院数据">
            <!-- 内容已折叠 -->
          </a-tab-pane>
          <a-tab-pane key="personal" tab="个人画像">
            <!-- 个人画像内容 -->
            <div class="portrait-container">
              <!-- 统一的查询区域 -->
              <div class="portrait-query-bar">
                <a-form layout="inline" @submit.prevent="generatePortrait">
                  <a-form-item label="人员ID">
                    <a-input v-model:value="userId" placeholder="输入教职工或学生ID" style="width: 240px;" />
                  </a-form-item>
                  <a-form-item>
                    <a-button type="primary" html-type="submit" :disabled="!userId">
                      <template #icon><SearchOutlined /></template>
                      生成画像
                    </a-button>
                  </a-form-item>
                  <a-form-item v-if="showPortrait">
                     <a-button @click="resetPortrait">
                      清空画像
                    </a-button>
                  </a-form-item>
                </a-form>
              </div>

              <!-- 个人画像展示区 -->
              <div v-if="showPortrait" class="portrait-display">
                <a-row :gutter="24">
                  <a-col :span="8">
                    <a-card :bordered="false" class="profile-card">
                      <div class="profile-header">
                        <a-avatar :size="80" :src="portrait.avatar" />
                        <h2 class="profile-name">{{ portrait.name }}</h2>
                        <p class="profile-position">{{ portrait.position }}</p>
                        <p class="profile-college">{{ portrait.college }}</p>
                      </div>
                      <div class="profile-tags">
                        <a-tag v-for="tag in portrait.tags" :key="tag" color="blue">{{ tag }}</a-tag>
                      </div>
                    </a-card>
                  </a-col>
                  <a-col :span="16">
                    <a-card :bordered="false" class="details-card">
                      <a-tabs>
                        <a-tab-pane key="ability" tab="综合能力">
                          <div class="tab-content-wrapper">
                            <div ref="radarChart" style="width: 100%; height: 300px;"></div>
                          </div>
                        </a-tab-pane>
                        <a-tab-pane key="projects" tab="科研项目">
                          <div class="tab-content-wrapper">
                            <a-descriptions bordered :column="1" size="small">
                              <a-descriptions-item v-for="proj in portrait.projects" :key="proj.name" :label="proj.name">
                                {{ proj.role }} ({{ proj.year }})
                              </a-descriptions-item>
                            </a-descriptions>
                          </div>
                        </a-tab-pane>
                        <a-tab-pane key="awards" tab="获奖情况">
                          <div class="tab-content-wrapper">
                            <a-timeline style="margin-top: 20px;">
                              <a-timeline-item v-for="award in portrait.awards" :key="award.name">
                                <strong>{{ award.name }}</strong> ({{ award.year }})
                              </a-timeline-item>
                            </a-timeline>
                          </div>
                        </a-tab-pane>
                      </a-tabs>
                    </a-card>
                  </a-col>
                </a-row>
              </div>
               <!-- 空状态提示 -->
              <div v-else class="portrait-entry-merged">
                <div class="entry-content">
                  <UserOutlined :style="{ fontSize: '48px', color: '#1890ff' }" />
                  <h3 style="margin: 16px 0;">生成个人画像</h3>
                  <p style="color: #888;">请输入教职工或学生的ID以生成详细的个人画像。</p>
                </div>
              </div>
            </div>
          </a-tab-pane>
          <a-tab-pane key="text-to-sql" tab="Text to SQL">
            <div class="text2sql-container-v2">
              <a-row :gutter="16">
                <!-- Left: DB Schema -->
                <a-col :span="6">
                  <a-card title="数据库结构" :bordered="false">
                    <a-tree
                      :tree-data="schemaTreeData"
                      default-expand-all
                      :show-line="true"
                      :show-icon="true"
                    >
                      <template #title="{ title, data }">
                        <span v-if="data.isTable" style="color: #1890ff; font-weight: bold;">{{ title }}</span>
                        <span v-else>{{ title }}</span>
                      </template>
                    </a-tree>
                  </a-card>
                </a-col>



                <!-- Middle: Main Workflow -->
                <a-col :span="12">
                  <div class="workflow-steps">
                    <a-steps direction="vertical" :current="sqlStep" size="small">
                      <a-step title="自然语言输入">
                        <template #description>
                          <a-card :bordered="false" style="margin-top: 8px;">
                            <a-textarea v-model:value="naturalQuery" :rows="3" placeholder="例如：查询石油工程学院的科研项目数量" />
                            <div style="margin-top: 12px; text-align: right;">
                              <a-button type="primary" @click="generateSql" :loading="sqlGenerating">
                                <template #icon><SendOutlined /></template>
                                生成SQL
                              </a-button>
                            </div>
                          </a-card>
                        </template>
                      </a-step>

                      <a-step title="生成的SQL">
                        <template #description>
                          <a-card :bordered="false" style="margin-top: 8px;">
                            <pre><code class="sql" v-html="highlightedSql"></code></pre>
                            <div style="margin-top: 12px; text-align: right;">
                              <a-button type="primary" @click="runSql" :disabled="!generatedSql" :loading="sqlRunning">
                                <template #icon><CaretRightOutlined /></template>
                                运行SQL
                              </a-button>
                            </div>
                          </a-card>
                        </template>
                      </a-step>

                      <a-step title="查询结果">
                        <template #description>
                           <a-card :bordered="false" style="margin-top: 8px;">
                            <a-table :columns="sqlResult.columns" :data-source="sqlResult.data" size="middle" :pagination="{ pageSize: 5 }">
                            </a-table>
                          </a-card>
                        </template>
                      </a-step>
                    </a-steps>
                  </div>
                </a-col>

                 <!-- Right: Query Examples & History -->
                <a-col :span="6">
                  <div class="query-examples-history-container">
                    <!-- <div class="section-header">
                      <DatabaseOutlined class="section-icon" />
                      <h4>查询示例与历史</h4>
                    </div> -->
                    
                    <!-- Query Examples -->
                    <div class="examples-section">
                      <h5 class="subsection-title">查询示例</h5>
                      <a-list 
                        :data-source="textToSqlExamples" 
                        size="small" 
                        class="examples-list"
                        :style="{ maxHeight: '180px', overflow: 'auto' }"
                      >
                        <template #renderItem="{ item }">
                          <a-list-item class="example-item">
                            <div class="example-content">
                              <div class="example-icon">
                                <BulbOutlined />
                              </div>
                              <div class="example-text">
                                <a @click="useSqlExample(item)" class="example-link">{{ item.query }}</a>
                              </div>
                            </div>
                          </a-list-item>
                        </template>
                      </a-list>
                    </div>
                    
                    <!-- Query History -->
                    <div class="history-section">
                      <h5 class="subsection-title">查询历史</h5>
                      <a-list
                        v-if="queryHistory.length > 0"
                        :data-source="queryHistory"
                        size="small"
                        class="history-list"
                        :style="{ maxHeight: '180px', overflow: 'auto' }"
                      >
                        <template #renderItem="{ item, index }">
                          <a-list-item class="history-item">
                            <div class="history-content">
                              <div class="history-index">{{ index + 1 }}</div>
                              <div class="history-text">
                                <a @click="useHistoryQuery(item)" class="history-link">{{ item }}</a>
                              </div>
                            </div>
                          </a-list-item>
                        </template>
                      </a-list>
                      <div v-else class="empty-history">
                        <a-empty description="暂无历史记录" />
                      </div>
                    </div>
                  </div>
                </a-col>
              </a-row>
            </div>
          </a-tab-pane>
        </a-tabs>
      </div>


      <!-- 数据展示区 (仅学院数据Tab显示) -->
      <div class="data-display" v-if="activeTab === 'college'">
        <a-row :gutter="16">
          <!-- 统计卡片 -->
          <a-col :span="6" v-for="stat in stats" :key="stat.title">
            <a-card :title="stat.title" :bordered="false">
              <div class="stat-value">{{ stat.value }}</div>
              <div class="stat-trend">
                <span :class="['trend', stat.trend > 0 ? 'up' : 'down']">
                  <img v-if="stat.trend > 0" src="/public/fire.svg" width="16" height="16" />
                  <img v-else src="/public/gas-fuel.png" width="16" height="16" />
                  {{ Math.abs(stat.trend) }}%
                </span>
                <span class="stat-desc">较上月</span>
              </div>
            </a-card>
          </a-col>
        </a-row>

        <!-- 图表区 -->
        <div class="chart-area">
          <a-row :gutter="16">
            <a-col :span="12">
              <a-card class="chart-card" :bordered="false">
                <div class="chart-card-header">
                  <span class="chart-icon trend-icon">
                    <svg width="28" height="28" viewBox="0 0 28 28"><defs><linearGradient id="trendGradient" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#4F8FFF"/><stop offset="100%" stop-color="#36cfc9"/></linearGradient></defs><circle cx="14" cy="14" r="14" fill="url(#trendGradient)"/><path d="M8 18l5-5 3 3 4-6" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round"/></svg>
                  </span>
                  <div>
                    <div class="chart-title">数据趋势</div>
                    <div class="chart-desc">近半年/年度数据变化趋势</div>
                  </div>
                </div>
                <a-tabs v-model:activeKey="trendChartType" class="chart-tabs">
                  <a-tab-pane key="monthly" tab="月度趋势">
                    <div class="chart-data" ref="monthlyTrendChart" style="width: 100%; height: 280px;"></div>
                  </a-tab-pane>
                  <a-tab-pane key="yearly" tab="年度趋势">
                    <div class="chart-data" ref="yearlyTrendChart" style="width: 100%; height: 280px;"></div>
                  </a-tab-pane>
                </a-tabs>
              </a-card>
            </a-col>
            <a-col :span="12">
              <a-card class="chart-card" :bordered="false">
                <div class="chart-card-header">
                  <span class="chart-icon dist-icon">
                    <svg width="28" height="28" viewBox="0 0 28 28">
                      <defs>
                        <radialGradient id="distGradient2" cx="50%" cy="50%" r="70%">
                          <stop offset="0%" stop-color="#fffbe6"/>
                          <stop offset="100%" stop-color="#faad14"/>
                        </radialGradient>
                        <linearGradient id="distGradient3" x1="0" y1="0" x2="1" y2="1">
                          <stop offset="0%" stop-color="#9254de"/>
                          <stop offset="100%" stop-color="#faad14"/>
                        </linearGradient>
                      </defs>
                      <circle cx="14" cy="14" r="14" fill="url(#distGradient2)"/>
                      <path d="M8 14a6 6 0 1 1 12 0" stroke="url(#distGradient3)" stroke-width="2.5" fill="none" stroke-linecap="round"/>
                      <circle cx="14" cy="14" r="4" fill="#fff" stroke="#9254de" stroke-width="1.5"/>
                    </svg>
                  </span>
                  <div>
                    <div class="chart-title">数据分布</div>
                    <div class="chart-desc">当前数据的结构分布情况</div>
                  </div>
                </div>
                <a-tabs v-model:activeKey="distChartType" class="chart-tabs">
                  <a-tab-pane key="pie" tab="饼图">
                    <div class="chart-data" ref="pieChart" style="width: 100%; height: 280px;"></div>
                  </a-tab-pane>
                  <a-tab-pane key="bar" tab="柱状图">
                    <div class="chart-data" ref="barChart" style="width: 100%; height: 280px;"></div>
                  </a-tab-pane>
                </a-tabs>
              </a-card>
            </a-col>
          </a-row>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, nextTick, watch, computed } from 'vue';
import { 
  ExportOutlined,
  SearchOutlined,
  UserOutlined,
  DatabaseOutlined,
  BulbOutlined,
  HistoryOutlined
} from '@ant-design/icons-vue';
import HeaderComponent from '@/components/HeaderComponent.vue';
import * as echarts from 'echarts';
import 'highlight.js/styles/atom-one-dark.css';
import hljs from 'highlight.js/lib/core';
import sql from 'highlight.js/lib/languages/sql';

hljs.registerLanguage('sql', sql);

const activeTab = ref('college');
const college = ref('');
const dataType = ref('party');
const userId = ref('');
const showPortrait = ref(false);

// Text to SQL
const naturalQuery = ref('');
const generatedSql = ref('');
const sqlGenerating = ref(false);
const sqlRunning = ref(false);
const sqlResult = reactive({
  columns: [],
  data: []
});
const queryHistory = ref([]);

const highlightedSql = computed(() => {
  if (generatedSql.value) {
    return hljs.highlight(generatedSql.value, { language: 'sql' }).value;
  }
  return '<span style="color: #777;">待生成</span>';
});

const schemaTreeData = ref([
  {
    title: 'research_projects',
    key: '0-0',
    isTable: true,
    children: [
      { title: 'project_id', key: '0-0-0' },
      { title: 'project_name', key: '0-0-1' },
      { title: 'college', key: '0-0-2' },
      { title: 'leader', key: '0-0-3' },
      { title: 'start_date', key: '0-0-4' },
    ],
  },
  {
    title: 'party_members',
    key: '0-1',
    isTable: true,
    children: [
      { title: 'member_id', key: '0-1-0' },
      { title: 'name', key: '0-1-1' },
      { title: 'college', key: '0-1-2' },
      { title: 'join_date', key: '0-1-3' },
    ],
  },
  {
    title: 'papers',
    key: '0-2',
    isTable: true,
    children: [
      { title: 'paper_id', key: '0-2-0' },
      { title: 'teacher_name', key: '0-2-1' },
      { title: 'publication_year', key: '0-2-2' },
    ],
  },
]);

const textToSqlExamples = ref([
  { 
    query: '查询石油工程学院的科研项目总数',
    sql: "SELECT COUNT(project_id) AS project_count\nFROM research_projects\nWHERE college = '石油工程学院';",
    result: {
      columns: [{ title: '项目总数', dataIndex: 'project_count', key: 'project_count' }],
      data: [{ key: '1', project_count: 158 }]
    }
  },
  { 
    query: '查找2023年以后入党的党员名单',
    sql: "SELECT name, college\nFROM party_members\nWHERE join_date > '2023-01-01';",
    result: {
      columns: [
        { title: '姓名', dataIndex: 'name', key: 'name' },
        { title: '学院', dataIndex: 'college', key: 'college' }
      ],
      data: [
        { key: '1', name: '王伟', college: '计算机科学学院' },
        { key: '2', name: '李静', college: '机械工程学院' }
      ]
    }
  },
  { 
    query: '统计每个学院的论文发表数量',
    sql: "SELECT p.college, COUNT(pa.paper_id) AS paper_count\nFROM party_members p\nJOIN papers pa ON p.name = pa.teacher_name\nGROUP BY p.college;",
    result: {
      columns: [
        { title: '学院', dataIndex: 'college', key: 'college' },
        { title: '论文数', dataIndex: 'paper_count', key: 'paper_count' }
      ],
      data: [
        { key: '1', college: '石油工程学院', paper_count: 78 },
        { key: '2', college: '地球科学学院', paper_count: 65 }
      ]
    }
  }
]);

const colleges = ref([
  '石油工程学院',
  '地球科学学院',
  '化学工程学院',
  '机械与储运工程学院',
  '信息科学与工程学院',
  '经济管理学院',
  '马克思主义学院'
]);

const stats = reactive([
  { title: '党员人数', value: '1,245', trend: 2.5 },
  { title: '科研项目', value: '328', trend: 5.2 },
  { title: '教学成果', value: '156', trend: -1.3 },
  { title: '学生人数', value: '12,856', trend: 3.7 }
]);

const trendChartType = ref('monthly');
const distChartType = ref('pie');

const monthlyTrendData = [
  { time: '1月', value: 1200, growth: '2.5%' },
  { time: '2月', value: 1250, growth: '4.2%' },
  { time: '3月', value: 1300, growth: '4.0%' },
  { time: '4月', value: 1350, growth: '3.8%' },
  { time: '5月', value: 1400, growth: '3.7%' },
  { time: '6月', value: 1450, growth: '3.6%' }
];

const yearlyTrendData = [
  { time: '2020年', value: 10000, growth: '5.2%' },
  { time: '2021年', value: 11000, growth: '10.0%' },
  { time: '2022年', value: 12500, growth: '13.6%' },
  { time: '2023年', value: 14000, growth: '12.0%' },
  { time: '2024年', value: 15000, growth: '7.1%' }
];

const pieData = [
  { category: '石油工程学院', value: 350, percentage: '28%' },
  { category: '地球科学学院', value: 280, percentage: '22%' },
  { category: '化学工程学院', value: 220, percentage: '18%' },
  { category: '机械与储运工程学院', value: 180, percentage: '14%' },
  { category: '其他学院', value: 215, percentage: '17%' }
];

const barData = [
  { category: '教授', value: 120, percentage: '10%' },
  { category: '副教授', value: 280, percentage: '22%' },
  { category: '讲师', value: 350, percentage: '28%' },
  { category: '助教', value: 180, percentage: '14%' },
  { category: '其他', value: 315, percentage: '25%' }
];

const portrait = reactive({
  name: '王教授',
  position: '博士生导师',
  college: '石油工程学院',
  avatar: '/avatar.jpg',
  tags: ['科研先锋', '教学名师', '优秀党员'],
  projects: [
    { name: '国家重点研发计划项目A', role: '负责人', year: 2022 },
    { name: '自然科学基金面上项目B', role: '负责人', year: 2020 },
    { name: '企业横向课题C', role: '技术骨干', year: 2021 },
  ],
  awards: [
    { name: '国家科技进步二等奖', year: 2021 },
    { name: '北京市教学成果一等奖', year: 2019 },
  ],
  ability: {
    indicator: [
      { name: '科研创新', max: 100 },
      { name: '教学水平', max: 100 },
      { name: '社会服务', max: 100 },
      { name: '团队协作', max: 100 },
      { name: '国际视野', max: 100 }
    ],
    data: [95, 92, 80, 88, 85]
  }
});

const queryData = () => {
  console.log('查询数据:', college.value, dataType.value);
  // 这里调用API获取数据
};

const generatePortrait = () => {
  if (!userId.value) return;
  showPortrait.value = true;
  console.log('生成个人画像:', userId.value);
  // 这里调用API生成画像
  nextTick(() => {
    initCharts();
  });
};

const resetPortrait = () => {
  showPortrait.value = false;
  userId.value = '';
};

const exportData = () => {
  console.log('导出数据');
  // 这里实现数据导出功能
};

const useSqlExample = (item) => {
  naturalQuery.value = item.query;
  generatedSql.value = item.sql;
  sqlResult.columns = item.result.columns;
  sqlResult.data = item.result.data;
};

const generateSql = () => {
  if (!naturalQuery.value) return;
  sqlGenerating.value = true;

  // Add to history
  if (!queryHistory.value.includes(naturalQuery.value)) {
    queryHistory.value.unshift(naturalQuery.value);
    if (queryHistory.value.length > 10) { // Keep last 10 queries
      queryHistory.value.pop();
    }
  }

  // 模拟API调用
  setTimeout(() => {
    const example = textToSqlExamples.value.find(e => e.query.includes(naturalQuery.value.substring(2, 5))) || textToSqlExamples.value[0];
    generatedSql.value = example.sql;
    sqlGenerating.value = false;
  }, 1000);
};

const useHistoryQuery = (query) => {
  naturalQuery.value = query;
  generateSql();
};

const runSql = () => {
  if (!generatedSql.value) return;
  sqlRunning.value = true;
  // 模拟API调用
  setTimeout(() => {
    const example = textToSqlExamples.value.find(e => e.sql === generatedSql.value) || textToSqlExamples.value[0];
    sqlResult.columns = example.result.columns;
    sqlResult.data = example.result.data;
    sqlRunning.value = false;
  }, 1500);
};

// Chart references
const monthlyTrendChart = ref(null);
const yearlyTrendChart = ref(null);
const pieChart = ref(null);
const barChart = ref(null);
const radarChart = ref(null);

let monthlyTrendChartInstance = null;
let categoryDistChartInstance = null;
let geoDistChartInstance = null;
let radarChartInstance = null;

const initCharts = () => {
  // 仅当在学院数据Tab时才初始化这些图表
  if (activeTab.value === 'college') {
    if (monthlyTrendChart.value) {
      const monthlyChart = echarts.init(monthlyTrendChart.value);
      monthlyChart.setOption({
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: monthlyTrendData.map(item => item.time) },
        yAxis: { type: 'value' },
        series: [{
          data: monthlyTrendData.map(item => item.value),
          type: 'line', smooth: true,
          lineStyle: { width: 3, color: '#1890ff' },
          itemStyle: { color: '#1890ff' }
        }]
      });
    }
    if (yearlyTrendChart.value) {
      const yearlyChart = echarts.init(yearlyTrendChart.value);
      yearlyChart.setOption({
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: yearlyTrendData.map(item => item.time) },
        yAxis: { type: 'value' },
        series: [{
          data: yearlyTrendData.map(item => item.value),
          type: 'line', smooth: true,
          lineStyle: { width: 3, color: '#1890ff' },
          itemStyle: { color: '#1890ff' }
        }]
      });
    }
    if (pieChart.value) {
      const pie = echarts.init(pieChart.value);
      pie.setOption({
        tooltip: { trigger: 'item' },
        series: [{
          name: '学院分布', type: 'pie', radius: ['40%', '70%'], avoidLabelOverlap: false,
          itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
          label: { show: true, formatter: '{b}: {d}%' },
          data: pieData.map(item => ({ value: item.value, name: item.category }))
        }]
      });
    }
    if (barChart.value) {
      const bar = echarts.init(barChart.value);
      bar.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'category', data: barData.map(item => item.category) },
        yAxis: { type: 'value' },
        series: [{
          data: barData.map(item => item.value), type: 'bar',
          itemStyle: { color: function(params) {
            const colorList = ['#1890ff', '#36cfc9', '#faad14', '#ff7a45', '#9254de'];
            return colorList[params.dataIndex % colorList.length];
          }}
        }]
      });
    }
  }

  // 仅在个人画像tab页初始化雷达图
  if (activeTab.value === 'personal' && showPortrait.value && radarChart.value) {
    radarChartInstance = echarts.init(radarChart.value);
    radarChartInstance.setOption({
      tooltip: { trigger: 'item' },
      radar: {
        indicator: portrait.ability.indicator
      },
      series: [{
        type: 'radar',
        data: [{
          value: portrait.ability.data,
          name: '综合能力评估'
        }]
      }]
    });
  }
};

const resizeCharts = () => {
  const charts = [
    echarts.getInstanceByDom(monthlyTrendChart.value),
    echarts.getInstanceByDom(yearlyTrendChart.value),
    echarts.getInstanceByDom(pieChart.value),
    echarts.getInstanceByDom(barChart.value),
    echarts.getInstanceByDom(radarChart.value)
  ].filter(c => c); // 过滤掉null
  charts.forEach(chart => chart.resize());
};

onMounted(() => {
  nextTick(() => {
    initCharts();
  });
  window.addEventListener('resize', resizeCharts);
});

onUnmounted(() => {
  window.removeEventListener('resize', resizeCharts);
});

watch(activeTab, (newTab) => {
  if (newTab === 'college') {
    nextTick(() => initCharts());
  }
});

watch(trendChartType, () => {
  nextTick(() => { initCharts(); });
});
watch(distChartType, () => {
  nextTick(() => { initCharts(); });
});
</script>

<style lang="less" scoped>
.datamining-container {
  padding: 0px,20px,20px,20px;
  // height: 100%;
  // overflow: hidden;
}

.dashboard {
  background: #fff;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.query-section {
  margin-bottom: 24px;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 8px;
}

.data-display {
  margin-top: 24px;
}

.stat-value {
  font-size: 24px;
  font-weight: bold;
  margin: 8px 0;
}

.stat-trend {
  display: flex;
  align-items: center;
  font-size: 14px;
  color: #666;

  .trend {
    margin-right: 8px;

    &.up {
      color: #52c41a;
    }

    &.down {
      color: #f5222d;
    }
  }
}

.chart-area {
  margin-top: 24px;
}
.chart-card {
  border-radius: 18px !important;
  box-shadow: 0 4px 24px 0 rgba(79,143,255,0.08);
  background: linear-gradient(135deg, #fafdff 0%, #f0f7ff 100%);
  padding: 0 0 12px 0;
  min-height: 410px;
}
.chart-card-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 18px 0 18px;
}
.chart-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 2px 8px rgba(79,143,255,0.10);
}
.trend-icon {
  background: linear-gradient(135deg, #e0f3ff 0%, #d1f7f3 100%);
}
.dist-icon {
  background: linear-gradient(135deg, #fff7e0 0%, #f3e0ff 100%);
}
.chart-title {
  font-size: 1.18rem;
  font-weight: bold;
  color: #2d3a4a;
}
.chart-desc {
  font-size: 0.98rem;
  color: #8ca0b3;
  margin-top: 2px;
}
.chart-tabs {
  margin-top: 8px;
  .ant-tabs-nav {
    margin-left: 18px;
  }
}

.chart-data {
  width: 100%;
  height: 100%;
}

.ant-tabs {
  height: 100%;
  
  .ant-tabs-content {
    height: calc(100% - 45px);
  }
}

.ant-card {
  height: 100%;
  
  .ant-card-body {
    height: calc(100% - 56px);
    padding: 16px;
  }
}

/* Portrait Styles */
.portrait-container {
  padding: 10px;
}
.portrait-query-bar {
  padding: 16px;
  background: #f5f7fa;
  border-radius: 8px;
  margin-bottom: 20px;
}
.portrait-entry-merged {
  display: flex;
  justify-content: center;
  align-items: center;
  text-align: center;
  min-height: 300px;
  background: #fdfdff;
  border-radius: 8px;
}
.entry-content {
  max-width: 400px;
}
.portrait-display {
  padding: 10px;
  background: #f9fafc;
  border-radius: 8px;
}
.profile-card {
  text-align: center;
  .profile-header {
    margin-bottom: 20px;
  }
  .profile-name {
    font-size: 22px;
    font-weight: 600;
    margin-top: 16px;
    margin-bottom: 4px;
  }
  .profile-position, .profile-college {
    color: #888;
    margin-bottom: 4px;
  }
  .profile-tags {
    margin-top: 20px;
    .ant-tag {
      margin: 4px;
    }
  }
}
.details-card {
  .ant-tabs-nav {
    padding-left: 10px;
  }
  .tab-content-wrapper {
    height: 320px; /* Or a suitable fixed height */
    overflow-y: auto;
    padding: 8px;
  }
}

/* Text to SQL Styles */
.text2sql-container {
  padding: 10px;
  .ant-card {
    margin-bottom: 0;
  }
  .ant-list-item {
    padding: 6px 0;
  }
  pre {
    background: #282c34;
    color: #abb2bf;
    padding: 1em;
    border-radius: 8px;
    white-space: pre-wrap;
    word-break: break-all;
    min-height: 90px;
    margin-bottom: 12px;
  }
}

/* Query Examples & History Container */
.query-examples-history-container {
  padding: 12px;
  background: white;
  border-radius: 8px;
  border: 1px solid #f0f0f0;
  height: 100%;
  display: flex;
  flex-direction: column;
  
  .section-header {
    display: flex;
    align-items: center;
    margin-bottom: 12px;
    flex-shrink: 0;
    
    .section-icon {
      font-size: 16px;
      color: #1890ff;
      margin-right: 8px;
    }
    
    h4 {
      margin: 0;
      font-weight: 600;
      color: #2d3a4a;
      font-size: 16px;
    }
  }
  
  .examples-section, .history-section {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
    
    .subsection-title {
      font-size: 14px;
      font-weight: 500;
      color: #595959;
      margin-bottom: 8px;
      padding-bottom: 4px;
      border-bottom: 1px solid #f0f0f0;
      flex-shrink: 0;
    }
  }
  
  .examples-section {
    margin-bottom: 12px;
  }
  
  .history-section {
    flex: 1;
  }
}

.examples-list {
  .example-item {
    padding: 0 !important;
    margin-bottom: 8px;
    
    .example-content {
      display: flex;
      align-items: flex-start;
      padding: 8px 12px;
      background: #fafafa;
      border-radius: 4px;
      cursor: pointer;
      transition: background-color 0.2s;
      
      &:hover {
        background: #e6f7ff;
      }
      
      .example-icon {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 20px;
        height: 20px;
        margin-right: 8px;
        flex-shrink: 0;
        
        .anticon {
          color: #1890ff;
          font-size: 12px;
        }
      }
      
      .example-text {
        flex: 1;
        
        .example-link {
          color: #2d3a4a;
          font-size: 13px;
          line-height: 1.4;
          text-decoration: none;
          display: block;
          
          &:hover {
            color: #1890ff;
          }
        }
      }
    }
  }
}

.history-list {
  .history-item {
    padding: 0 !important;
    margin-bottom: 6px;
    
    .history-content {
      display: flex;
      align-items: center;
      padding: 6px 8px;
      background: #fafafa;
      border-radius: 4px;
      cursor: pointer;
      transition: background-color 0.2s;
      
      &:hover {
        background: #f6ffed;
      }
      
      .history-index {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 18px;
        height: 18px;
        background: #e8e8e8;
        border-radius: 3px;
        font-size: 10px;
        font-weight: 500;
        color: #666;
        margin-right: 8px;
        flex-shrink: 0;
      }
      
      .history-text {
        flex: 1;
        
        .history-link {
          color: #666;
          font-size: 12px;
          line-height: 1.3;
          text-decoration: none;
          display: block;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
      }
    }
  }
}

.empty-history {
  text-align: center;
  padding: 16px 0;
  
  .ant-empty {
    .ant-empty-image {
      height: 40px;
      margin-bottom: 8px;
    }
    
    .ant-empty-description {
      color: #8ca0b3;
      font-size: 12px;
    }
  }
}

.ant-tabs-tab {
  padding: 8px 16px !important;
}
</style>
