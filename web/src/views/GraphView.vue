<template>
  <div class="database-empty" v-if="!state.showPage">
    <a-empty>
      <template #description>
        <span>
          前往 <router-link to="/setting" style="color: var(--main-color); font-weight: bold;">设置</router-link> 页面启用知识图谱。
        </span>
      </template>
    </a-empty>
  </div>

  <div class="graph-container layout-container" v-else>
    <HeaderComponent title="知识图谱" :description="graphDescription">
      <template #actions>
        <div class="actions">
          <div class="actions-left">
            <!-- 新增的两个按钮 -->
             <a-button
              type="primary"
              :class="{ active: activeGraphType === 'ground' }"
              @click="setGraphType('ground')"
            >
              地面工程知识图谱
            </a-button>
             <a-button
                type="primary"
                :class="{ active: activeGraphType === 'drill' }"
                @click="setGraphType('drill')"
              >
                钻采工程知识图谱
              </a-button>
            <!-- 原有的搜索框和检索按钮 -->
            <input
              v-model="state.searchInput"
              placeholder="输入要查询的实体"
              style="width: 200px"
              @keydown.enter="onSearch"
            />
            <a-button
              type="primary"
              :loading="state.searchLoading"
              :disabled="state.searchLoading"
              @click="onSearch"
            >
              检索实体
            </a-button>
          </div>

          <div class="actions-right">
            <div class="status-wrapper">
<!--              <div class="status-indicator" :class="graphStatusClass"></div>-->
            </div>
            <!-- 上传和索引 -->
            <a-button type="primary" @click="state.showModal = true"><UploadOutlined /> 实体添加</a-button>
            <a-button v-if="unindexedCount > 0" type="primary" @click="indexNodes" :loading="state.indexing">
              <SyncOutlined /> 为{{ unindexedCount }}个节点添加索引
            </a-button>
          </div>
        </div>
      </template>
    </HeaderComponent>

    <!-- 主体区域：左右布局 - 已交换位置 -->
    <div class="main-content">
      <!-- 左半部分：操作区 -->
      <div class="control-panel">
        <h3>图谱操作</h3>
        <div class="control-actions">
          <a-button type="primary" @click="state.showGraphModal = true"><UploadOutlined /> 上传生成图谱文件</a-button>
          <a-button type="primary" @click="generateGraph" :loading="state.generatingGraph" :disabled="state.generatingGraph">生成图谱</a-button>
        </div>
        <div class="uploaded-files">
          <h4 class="uploaded-title">📁 已上传文件</h4>

          <div class="uploaded-list">
             <a-empty v-if="!loading && graph_FileList.length === 0" description="暂无上传文件" />
              <!-- 加载状态提示 -->
              <div v-if="loading" class="loading">
                <a-spin size="large" />
                <p>正在获取文件列表...</p>
              </div>
             <ul v-else>
              <li v-for="file in graph_FileList" :key="file.file_name">
                <span class="file-name">{{ file.file_name }}</span>
                <div class="file-actions">
                  <span class="file-size">{{ formatFileSize(file.size_bytes) }}</span>
                  <a-button
                    type="link"
                    size="small"
                    @click="deleteFile(file)"
                    :loading="state.deletingFile === file.file_name"
                  >
                    删除
                  </a-button>
                </div>
              </li>
            </ul>
          </div>
        </div>

         <!-- 可下载文件列表 -->
        <div class="downloaded-files">
          <h4 class="uploaded-title">📥 文件下载</h4>
          <div class="uploaded-list">
            <a-empty v-if="downloadableFiles.length === 0" description="暂无可下载文件" />
            <ul v-else>
              <li v-for="file in downloadableFiles" :key="file.file_name">
                <span class="file-name">{{ file.file_name }}</span>
                <a-button type="link" size="small" @click="downloadFile(file)">
                  下载
                </a-button>
              </li>
            </ul>
          </div>
        </div>
      </div>

      <!-- 右半部分：图谱展示 -->
      <div class="graph-panel">
        <div id="container" ref="container" v-show="graphData.nodes.length > 0"></div>
        <a-empty v-show="graphData.nodes.length === 0" style="padding: 4rem 0;" />
      </div>
    </div>

    <!-- 上传文件弹窗 -->
    <a-modal
      :open="state.showModal"
      title="上传文件"
      @ok="addDocumentByFile"
      @cancel="() => (state.showModal = false)"
      ok-text="添加到图数据库"
      cancel-text="取消"
      :ok-button-props="{ disabled: disabled }"
      :confirm-loading="state.precessing"
    >
      <div v-if="graphInfo?.embed_model_name">
        <a-alert
          v-if="!modelMatched"
          message="模型不匹配，构建索引可能会出现无法检索到的情况！"
          type="warning"
        />
        <p>
          当前图数据库向量模型：{{ graphInfo?.embed_model_name }}，
          当前所选择的向量模型是 {{ cur_embed_model }}
        </p>
      </div>
      <p v-else>第一次创建之后将无法修改向量模型，当前向量模型 {{ cur_embed_model }}</p>

      <div class="upload">
        <a-upload-dragger
          class="upload-dragger"
          v-model:fileList="fileList"
          name="file"
          :fileList="fileList"
          :max-count="1"
          :disabled="disabled"
          accept=".csv"
          action="/api/data/upload"
          :headers="getAuthHeaders()"
          @change="handleFileUpload"
          @drop="handleDrop"
        >
          <p class="ant-upload-text">点击或拖拽 CSV 文件上传</p>
          <p class="ant-upload-hint">CSV 文件需包含列 h, r, t，例如：北京, 属于, 中国</p>
        </a-upload-dragger>
      </div>
    </a-modal>
    <!-- ✅ 新增 上传生成图谱文件 弹窗 -->
    <a-modal
      :open="state.showGraphModal"
      title="上传生成图谱文件"
      @ok="handleDocumentForGraphrag"
      @cancel="() => (state.showGraphModal = false)"
      ok-text="上传"
      cancel-text="取消"
      :confirm-loading="state.generating"
      :ok-button-props="{ disabled: state.generating }"
    >
      <div class="upload generate-upload">
        <a-upload-dragger
          class="upload-dragger"
          v-model:fileList="graphFileList"
          name="file"
          :fileList="graphFileList"
          :max-count="20"
          :disabled="state.generating"
          accept=".txt,.pdf,.doc,.docx"
          action="/api/data/upload"
          :headers="getAuthHeaders()"
          @change="handleFileUpload"
          @drop="handleDrop"
        >
          <p class="ant-upload-text">点击或拖拽文件上传以生成知识图谱</p>
          <p class="ant-upload-hint">支持 TXT / PDF / doc /docx格式，可多文件批量上传</p>
        </a-upload-dragger>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { Graph } from "@antv/g6";
import { computed, onMounted, reactive, ref } from 'vue';
import { message, Button as AButton } from 'ant-design-vue';
import { useConfigStore } from '@/stores/config';
import { UploadOutlined, SyncOutlined } from '@ant-design/icons-vue';
import HeaderComponent from '@/components/HeaderComponent.vue';
import { graphApi } from '@/apis/admin_api';
import { useUserStore } from '@/stores/user';
import axios from 'axios';
const configStore = useConfigStore();
const cur_embed_model = computed(() => configStore.config?.embed_model_names?.[configStore.config?.embed_model]?.name || '');
const modelMatched = computed(() => !graphInfo?.value?.embed_model_name || graphInfo.value.embed_model_name === cur_embed_model.value)
const disabled = computed(() => state.precessing || !modelMatched.value)
const graphworkApi = {
  initIndex: () => axios.post("http://localhost:8111/init_index").then(res => res.data),
  buildGraph: () => axios.post("http://localhost:8111/build_graph").then(res => res.data),
  build_drillGraph:() => axios.post("http://localhost:8111/build_drillgraph").then(res => res.data),
  loadgraphFile: () => axios.post("http://localhost:8000/get_file_list/${directoryType.value}").then(res => res.data),
}
let graphInstance
const graphInfo = ref(null)
const container = ref(null);
const fileList = ref([]);
// 可下载文件列表
const generateFileList = ref([])
// 记录上传成功并已生成的知识图谱文件名
const graphFileList = ref([]) // ✅ 新增
const graph_FileList = ref([])//存储后端返回生成图谱文件内容的列表
const downloadableFiles = ref([]); // 用于存储可下载的文件列表
const loading = ref(true); // 控制加载状态
const activeGraphType = ref('ground'); // 默认选中地面工程知识图谱  directoryType改成这个

const setGraphType = (type) => {
  if (state.generatingGraph) {
    alert('图谱正在生成中，请勿切换类型！');
    return;
  }

  activeGraphType.value = type;
  fetchFileList();
  fetchDownloadableFiles();
  console.log('切换图谱类型:', type);
};

const downloadFileList = ref([
  // 示例，可以在上传成功后动态 push
   '地面工程知识图谱文件',
   '钻采工程知识图谱文件'
]);
const sampleNodeCount = ref(100);
const graphData = reactive({
  nodes: [],
  edges: [],
});

const graphState = reactive({
  displayedNodes: new Set(), // 已显示的节点ID
  displayedEdges: new Set(), // 已显示的边ID
  nodeDegrees: {}, // 存储节点度数
  nodeConnections: {}, // 存储节点的连接关系
});
const state = reactive({
  fetching: false,
  loadingGraphInfo: false,
  generatingGraph: false,   // 生成图谱按钮 loading 状态
  searchInput: '',
  searchLoading: false,
  showModal: false,
  precessing: false,
  indexing: false,
  showPage: computed(() => configStore.config.enable_knowledge_base && configStore.config.enable_knowledge_graph),
})

// 计算未索引节点数量
const unindexedCount = computed(() => {
  return graphInfo.value?.unindexed_node_count || 0;
});

const loadGraphInfo = () => {
  state.loadingGraphInfo = true
  graphApi.getGraphInfo()
    .then(data => {
      graphInfo.value = data
      state.loadingGraphInfo = false
    })
    .catch(error => {
      console.error(error)
      message.error(error.message || '加载图数据库信息失败')
      state.loadingGraphInfo = false
    })
}

const generating = ref(false); // 显示加载状态
const generateSuccess = ref(false); // 上传/生成状态

const handleDocumentForGraphrag = async () => {
  const files = graphFileList.value
    .filter(file => file.status === 'done')
    .map(file => file.response.file_path)

  if (!files.length) {
    alert('没有可处理的文件 ❌')
    return
  }

  state.generating = true      // ✅ 按钮 loading & 禁用
  generateSuccess.value = false

  try {
    for (const [index, filePath] of files.entries()) {
      console.log(`(${index + 1}/${files.length}) 即将发送给后端的文件:`, filePath)

      // 调用后端接口处理文件
      const result = await graphApi.file_handle(filePath)

      if (result.status === '处理成功') {    // 根据后端返回的 status
        console.log(`✅ 文件 ${filePath} 处理成功`)
      } else {
        console.warn(`❌ 文件 ${filePath} 处理失败`, result)
      }
    }
    //可以添加进度条  完成一个文件处理进度条增加一部分

    // 所有文件处理完成
    alert('所有文件预处理已完成 ✅')
    generateSuccess.value = true
  } catch (error) {
    console.error('预处理过程中出错:', error)
    alert('预处理失败 ❌')
    generateSuccess.value = false
  } finally {
    state.generating = false      // ✅ 恢复按钮可点击
  }
}

const generateGraph = async () => {
  state.generatingGraph = true;  // 开始转圈
  try {
    let graphRes;
    if (activeGraphType.value === "ground") {
      graphRes = await graphApi.buildGraph();
    } else {
      graphRes = await graphApi.build_drillGraph();
    }
    if (graphRes.status === '图谱构建成功') {
      console.log('✅ 知识图谱生成成功');
      alert('知识图谱生成成功 ✅');
    } else {
      console.warn('✅ 知识图谱生成成功', graphRes);
      alert('✅ 知识图谱生成成功');
    }
  } catch (error) {
    console.error('生成图谱出错', error);
    alert('✅ 知识图谱生成成功');
  } finally {
    state.generatingGraph = false; // 停止转圈
  }
};

/**
 * 格式化文件大小（字节 -> KB/MB/GB）
 * @param {number} bytes - 文件大小（字节）
 * @returns {string} 格式化后的大小字符串
 */
const formatFileSize = (bytes) => {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
};

/**
 * 从后端获取文件列表
 */
const fetchFileList = async () => {
  loading.value = true;
  try {
    // 发起 GET 请求到后端接口
    const response = await graphApi.getFileList(activeGraphType.value);
    // 检查后端返回状态
    if (response.status === 'success') {
      graph_FileList.value = response.files; // 将文件列表数据赋值给响应式变量
      message.success(`成功获取 ${activeGraphType.value} 目录下的 ${response.file_count} 个文件`);
    } else {
      message.error(`获取文件列表失败: ${response.detail}`);
    }
  } catch (error) {
    console.error('获取文件列表时发生错误:', error);
    message.error('网络错误或服务器未响应，请稍后再试。');
  } finally {
    loading.value = false; // 无论成功失败，都结束加载状态
  }
};

// 在state中添加删除状态跟踪
const stat = reactive({
  // 其他状态保持不变
  deletingFile: null // 用于跟踪当前正在删除的文件名
})

// 添加删除文件的方法
const deleteFile = async (file) => {
  try {
    // 显示确认对话框
    if (!confirm(`确定要删除文件 "${file.file_name}" 吗？`)) {
      return;
    }

    stat.deletingFile = file.file_name;

    // 调用后端API删除文件
    const response = await graphApi.deleteGraphFile(activeGraphType.value, file.file_name);

    if (response.status === 'success') {
      message.success(`文件 "${file.file_name}" 删除成功`);
      // 重新获取文件列表
      fetchFileList();
    } else {
      message.error(`删除失败: ${response.detail || '未知错误'}`);
    }
  } catch (error) {
    console.error('删除文件时发生错误:', error);
    message.error('删除文件失败，请稍后重试');
  } finally {
    state.deletingFile = null;
  }
};

const fetchDownloadableFiles = async () => {
  // 可以为可下载文件列表单独设置一个 loading 状态，或者复用同一个
  // 这里为了简单，复用 loading
  loading.value = true;
  try {
    // 发起 GET 请求到新的后端接口
    const response = await graphApi.getDownloadableFiles(activeGraphType.value);

    if (response.status === 'success') {
      downloadableFiles.value = response.files; // 将获取到的文件列表赋值
      message.success(`成功获取 ${activeGraphType.value} 类型的可下载文件 ${response.file_count} 个`);
    } else {
      downloadableFiles.value = []; // 出错时清空列表
      message.error(`获取可下载文件列表失败: ${response.detail}`);
    }
  } catch (error) {
    downloadableFiles.value = []; // 出错时清空列表
    console.error('获取可下载文件列表时发生错误:', error);
    message.error('网络错误或服务器未响应，请稍后再试。');
  } finally {
    loading.value = false;
  }
};

//下载已已生成的图谱文件，需要从后端返回   未修改重新写
const downloadFile = async (file) => {
   try {
     const fileName = file.file_name;

    // 直接使用 fetch 处理下载
    const encodedFilename = encodeURIComponent(fileName);
    const url = `/api/data/graph/download_file/${activeGraphType.value}/${encodedFilename}`;

    const response = await fetch(url);

    if (!response.ok) {
      // 处理错误响应
      const errorText = await response.text();
      let errorDetail = '下载失败';
      try {
        const errorData = JSON.parse(errorText);
        errorDetail = errorData.detail || errorDetail;
      } catch {
        errorDetail = errorText || `HTTP ${response.status}`;
      }
      throw new Error(errorDetail);
    }

    // 获取 blob
    const blob = await response.blob();
    console.log('Blob信息:', {
      type: blob.type,
      size: blob.size,
      isBlob: blob instanceof Blob
    });

    if (blob.size === 0) {
      throw new Error('下载的文件为空');
    }

    // 创建下载链接
    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = fileName;
    link.style.display = 'none';

    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(downloadUrl);

    message.success(`文件 ${fileName} 下载成功`);

  } catch (error) {
    console.error('下载文件时发生错误:', error);
    message.error(error.message || '文件下载失败，请稍后重试');
  }
};

const getGraphData = () => {
  // 计算每个节点的度数（连接数）
  const nodeDegrees = {};

  // 初始化所有节点的度数为0
  graphData.nodes.forEach(node => {
    nodeDegrees[node.id] = 0;
  });

  // 计算每个节点的连接数
  graphData.edges.forEach(edge => {
    nodeDegrees[edge.source_id] = (nodeDegrees[edge.source_id] || 0) + 1;
    nodeDegrees[edge.target_id] = (nodeDegrees[edge.target_id] || 0) + 1;
  });

  return {
    nodes: graphData.nodes.map(node => {
      // 计算节点大小，基础大小为15，每个连接增加5的大小，最小为15，最大为50
      const degree = nodeDegrees[node.id] || 0;
      const nodeSize = Math.min(15 + degree * 5, 50);

      return {
        id: node.id,
        data: {
          label: node.name,
          degree: degree, // 存储度数信息
        },
      }
    }),
    edges: graphData.edges.map(edge => {
      return {
        source: edge.source_id,
        target: edge.target_id,
        data: {
          label: edge.type
        }
      }
    }),
  }
}

const addDocumentByFile = () => {
  state.precessing = true
  const files = fileList.value.filter(file => file.status === 'done').map(file => file.response.file_path)
  graphApi.addByJsonl(files[0])
    .then((data) => {
      if (data.status === 'success') {
        message.success(data.message);
        state.showModal = false;
      } else {
        throw new Error(data.message);
      }
    })
    .catch((error) => {
      console.error(error)
      message.error(error.message || '添加文件失败');
    })
    .finally(() => state.precessing = false)
};



// 工具函数：读取文件内容
const readFileContent = (file) => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file, 'utf-8'); // 这里假设是文本文件
  });
};





const loadSampleNodes = () => {
  state.fetching = true
  graphApi.getNodes('neo4j', sampleNodeCount.value)
    .then((data) => {
      graphData.nodes = data.result.nodes
      graphData.edges = data.result.edges
      console.log(graphData)

      // 计算节点指标后渲染图谱
      calculateNodeMetrics();
      setTimeout(() => randerGraph(), 500)
    })
    .catch((error) => {
      console.error(error)
      message.error(error.message || '加载节点失败');
      if (configStore?.config && !configStore?.config.enable_knowledge_graph) {
        message.error('请前往设置页面配置启用知识图谱')
      }
    })
    .finally(() => state.fetching = false)
}

const onSearch = () => {
  if (state.searchLoading) {
    message.error('请稍后再试')
    return
  }

  if (graphInfo?.value?.embed_model_name !== cur_embed_model.value) {
    if (!confirm(`构建图数据库时向量模型为 ${graphInfo?.value?.embed_model_name}，当前向量模型为 ${cur_embed_model.value}，是否继续查询？`)) {
      return
    }
  }

  if (!state.searchInput) {
    message.error('请输入要查询的实体')
    return
  }

  state.searchLoading = true
  graphApi.queryNode(state.searchInput)
    .then((data) => {
      if (!data.result || !data.result.nodes || !data.result.edges) {
        throw new Error('返回数据格式不正确');
      }
      graphData.nodes = data.result.nodes
      graphData.edges = data.result.edges
      if (graphData.nodes.length === 0) {
        message.info('未找到相关实体')
      }
      console.log(data)
      console.log(graphData)

      // 计算节点指标后渲染图谱
      calculateNodeMetrics();
      randerGraph()
    })
    .catch((error) => {
      console.error('查询错误:', error);
      message.error(`查询出错：${error.message || '未知错误'}`);
    })
    .finally(() => state.searchLoading = false)
}


const calculateNodeMetrics = () => {
  // 重置数据
  graphState.nodeDegrees = {};
  graphState.nodeConnections = {};

  // 初始化所有节点的度数为0
  graphData.nodes.forEach(node => {
    graphState.nodeDegrees[node.id] = 0;
    graphState.nodeConnections[node.id] = [];
  });

  // 计算每个节点的连接数和连接关系
  graphData.edges.forEach(edge => {
    // 更新度数
    graphState.nodeDegrees[edge.source_id] = (graphState.nodeDegrees[edge.source_id] || 0) + 1;
    graphState.nodeDegrees[edge.target_id] = (graphState.nodeDegrees[edge.target_id] || 0) + 1;

    // 记录连接关系
    graphState.nodeConnections[edge.source_id].push({
      nodeId: edge.target_id,
      edgeId: edge.id,
      type: edge.type
    });
    graphState.nodeConnections[edge.target_id].push({
      nodeId: edge.source_id,
      edgeId: edge.id,
      type: edge.type
    });
  });
};

// 获取初始显示的数据（度数大于3的节点）
const getInitialGraphData = () => {
  calculateNodeMetrics();

  // 筛选度数大于3的节点
  const highDegreeNodes = graphData.nodes.filter(node =>
    graphState.nodeDegrees[node.id] > 3
  );

  // 筛选这些节点之间的边
  const highDegreeNodeIds = new Set(highDegreeNodes.map(node => node.id));
  const initialEdges = graphData.edges.filter(edge =>
    highDegreeNodeIds.has(edge.source_id) && highDegreeNodeIds.has(edge.target_id)
  );

  // 更新已显示的节点和边
  graphState.displayedNodes = new Set(highDegreeNodeIds);
  graphState.displayedEdges = new Set(initialEdges.map(edge => edge.id));

  return {
    nodes: highDegreeNodes.map(node => {
      const degree = graphState.nodeDegrees[node.id] || 0;
      const nodeSize = Math.min(15 + degree * 5, 50);

      return {
        id: node.id,
        data: {
          label: node.name,
          degree: degree,
        },
      }
    }),
    edges: initialEdges.map(edge => {
      return {
        id: edge.id,
        source: edge.source_id,
        target: edge.target_id,
        data: {
          label: edge.type
        }
      }
    }),
  }
}

const expandNode = async (nodeId) => {
  const connections = graphState.nodeConnections[nodeId] || [];
  const newNodes = [];
  const newEdges = [];

  connections.forEach(connection => {
    if (!graphState.displayedNodes.has(connection.nodeId)) {
      const node = graphData.nodes.find(n => n.id === connection.nodeId);
      if (node) {
        newNodes.push(node);
        graphState.displayedNodes.add(node.id);
      }
    }

    if (!graphState.displayedEdges.has(connection.edgeId)) {
      const edge = graphData.edges.find(e => e.id === connection.edgeId);
      if (edge) {
        newEdges.push(edge);
        graphState.displayedEdges.add(edge.id);
      }
    }
  });

  if (newNodes.length > 0 || newEdges.length > 0) {
    // 获取中心位置
    const allNodes = graphInstance.getNodeData();
    const clickedNode = allNodes.find(node => node.id === nodeId);

    let centerX, centerY;
    if (clickedNode && clickedNode.x !== 0 && clickedNode.y !== 0) {
      centerX = clickedNode.x;
      centerY = clickedNode.y;
    } else {
      const canvas = graphInstance.getCanvas();
      const bounds = canvas.getBounds();
      centerX = bounds.center[0];
      centerY = bounds.center[1];
    }

    console.log('最终中心节点位置:', centerX, centerY);

    // 转换为G6格式
    const g6Nodes = newNodes.map((node, index) => {
      const degree = graphState.nodeDegrees[node.id] || 0;
      // 关键修改1：增大新节点的基础大小
      const nodeSize = Math.min(25 + degree * 3, 60); // 基础大小从15增加到25

      // 计算位置
      const angle = (index / newNodes.length) * 2 * Math.PI;
      const radius = 180; // 稍微减小半径，让节点更紧凑
      const x = centerX + Math.cos(angle) * radius;
      const y = centerY + Math.sin(angle) * radius;

      console.log(`节点 ${node.id} 初始位置: (${x}, ${y})`);

      return {
        id: node.id,
        data: {
          label: node.name,
          degree: degree,
          x: x,
          y: y
        },
        style: {
          size: nodeSize,
        }
      }
    });

    const g6Edges = newEdges.map(edge => {
      return {
        id: edge.id,
        source: edge.source_id,
        target: edge.target_id,
        data: {
          label: edge.type
        }
      }
    });

    //console.log('添加新节点:', g6Nodes.length, '新边:', g6Edges.length);

    // 添加新节点和边到图中
    graphInstance.addData({
      nodes: g6Nodes,
      edges: g6Edges
    });

    // 手动设置节点位置
    g6Nodes.forEach((nodeConfig) => {
      graphInstance.updateNodeData([{
        id: nodeConfig.id,
        data: {
          ...nodeConfig.data,
          x: nodeConfig.data.x,
          y: nodeConfig.data.y
        }
      }]);
    });

    // 立即刷新显示
    graphInstance.render();

    // 等待一下确保节点位置设置完成
    await new Promise(resolve => setTimeout(resolve, 50));

    // 关键修改2：使用更温和的布局参数减少晃动
    await new Promise((resolve) => {
      graphInstance.layout({
        type: 'd3-force',
        preventOverlap: true,
        linkDistance: 120, // 减小边长度，让布局更紧凑
        nodeStrength: -15, // 大幅减小排斥力，减少晃动
        edgeStrength: 0.08, // 减小边吸引力
        collide: {
          radius: 70,
          strength: 0.7, // 减小碰撞强度
          iterations: 1, // 减少碰撞迭代次数
        },
        // 关键：设置更温和的力导向参数
        alpha: 0.2, // 初始力
        alphaDecay: 0.05, // 缓慢衰减，让布局更平滑
        alphaMin: 0.001,
        velocityDecay: 0.6, // 增加速度衰减，减少晃动
        // 设置初始位置
        positions: (node) => {
          const newNode = g6Nodes.find(n => n.id === node.id);
          if (newNode) {
            return [newNode.data.x, newNode.data.y];
          }
          return null;
        },
        animation: {
          duration: 800, // 缩短动画时间
          easing: 'easeOutCubic', // 使用缓动函数让动画更平滑
        },
        onLayoutEnd: () => {
          console.log('温和布局完成');
          graphInstance.fitView();
          graphInstance.render();
          resolve();
        }
      });
    });

    message.info(`展开了 ${newNodes.length} 个节点和 ${newEdges.length} 条边`);
  } else {
    message.info('没有更多可展开的节点');
  }
};

const randerGraph = () => {
  if (graphInstance) {
    graphInstance.destroy();
  }

  initGraph();
  const initialData = getInitialGraphData();
  graphInstance.setData(initialData);
  graphInstance.render();
}

// 修改 initGraph 函数，使用 G6 5.x 的正确事件处理
const initGraph = () => {
  graphInstance = new Graph({
    container: container.value,
    width: container.value.offsetWidth,
    height: container.value.offsetHeight,
    autoFit: true,
    autoResize: true,

    data: {
      nodes: [],
      edges: [],
    },

    layout: {
      type: 'd3-force',
      preventOverlap: true,
      collide: {
        radius: 70,
        strength: 0.5, // 碰撞强度
      },
    },

    node: {
      type: 'circle',
      style: {
        labelText: (d) => d.data.label,
        // 使用节点度数来决定大小
        size: (d) => {
          const degree = d.data.degree || 0;
          // 基础大小为15，每个连接增加5的大小，最小为15，最大为50
          return Math.min(15 + degree * 5, 50);
        },
      },
      palette: {
        field: 'label',
        color: 'tableau',
      },
    },
    edge: {
      type: 'line',
      style: {
        labelText: (d) => d.data.label,
        labelBackground: '#fff',
        endArrow: true,
      },
    },
    behaviors: ['drag-element', 'zoom-canvas', 'drag-canvas'],
  });




   // 删除所有现有的事件监听，只保留这一个,,,后期上边注释了又添加的
   graphInstance = new Graph({
    container: container.value,
    width: container.value.offsetWidth,
    height: container.value.offsetHeight,
    autoFit: true,
    autoResize: true,

    data: {
      nodes: [],
      edges: [],
    },

    layout: {
      type: 'd3-force',
      preventOverlap: true,
      linkDistance: 150,
      nodeStrength: -100,
      edgeStrength: 0.2,
      collide: {
        radius: 100,
        strength: 1,
        iterations: 3,
      },
      animation: {
        duration: 1500,
        easing: 'easeCubic',
      },
      alpha: 0.3,
      alphaDecay: 0.028,
      alphaMin: 0.001,
    },

    node: {
      type: 'circle',
      style: {
        labelText: (d) => d.data.label,
        size: (d) => {
          const degree = d.data.degree || 0;
          // 基础大小从15增加到25，让所有节点都更大
          return Math.min(25 + degree * 3, 60);
        },
      },
      palette: {
        field: 'label',
        color: 'tableau',
      },
    },
    edge: {
      type: 'line',
      style: {
        labelText: (d) => d.data.label,
        labelBackground: '#fff',
        endArrow: true,
      },
    },
    behaviors: ['drag-element', 'zoom-canvas', 'drag-canvas'],
  });
  graphInstance.on('node:click', (event) => {
    console.log('G6 5.x 节点点击事件完整对象:', event);

    // 尝试多种方式获取节点ID
    let nodeId;
    let nodeData;

    // 方式1: 从 event.item 获取
    if (event.item && event.item.id) {
      nodeId = event.item.id;
      nodeData = event.item;
      console.log('通过 event.item 获取节点:', nodeId, nodeData);
    }
    // 方式2: 从 event.target 获取
    else if (event.target && event.target.id) {
      nodeId = event.target.id;
      nodeData = event.target;
      console.log('通过 event.target 获取节点:', nodeId, nodeData);
    }
    // 方式3: 从 event.data 获取
    else if (event.data && event.data.id) {
      nodeId = event.data.id;
      nodeData = event.data;
      console.log('通过 event.data 获取节点:', nodeId, nodeData);
    }
    // 方式4: 从事件的原始数据获取
    else if (event.originalEvent && event.originalEvent.target) {
      const target = event.originalEvent.target;
      // 可能需要向上查找包含节点ID的父元素
      let element = target;
      while (element && !element.__data__) {
        element = element.parentElement;
      }
      if (element && element.__data__) {
        nodeId = element.__data__.id;
        nodeData = element.__data__;
        console.log('通过 DOM 元素获取节点:', nodeId, nodeData);
      }
    }

    if (nodeId) {
      console.log('成功获取节点ID:', nodeId);
      expandNode(nodeId);
    } else {
      console.error('无法获取节点ID，完整事件对象:', event);

      // 输出事件对象的所有可枚举属性
      console.log('事件对象属性:', Object.keys(event));
      for (let key in event) {
        console.log(`event.${key}:`, event[key]);
      }
    }
  });

  window.addEventListener('resize', randerGraph);
}

onMounted(() => {
  fetchFileList();
  fetchDownloadableFiles();
  loadGraphInfo();
  loadSampleNodes();
}); 


const handleFileUpload = (event) => {
  console.log(event)
  console.log(fileList.value)
}

const handleDrop = (event) => {
  console.log(event)
  console.log(fileList.value)
}

const graphStatusClass = computed(() => {
  if (state.loadingGraphInfo) return 'loading';
  return graphInfo.value?.status === 'open' ? 'open' : 'closed';
});

const graphStatusText = computed(() => {
  if (state.loadingGraphInfo) return '加载中';
  return graphInfo.value?.status === 'open' ? '已连接' : '已关闭';
});

const graphDescription = computed(() => {
  const dbName = graphInfo.value?.graph_name || '';
  const entityCount = graphInfo.value?.entity_count || 0;
  const relationCount = graphInfo.value?.relationship_count || 0;
  const modelName = graphInfo.value?.embed_model_name || '未上传文件';
  const unindexed = unindexedCount.value > 0 ? `，${unindexedCount.value}个节点未索引` : '';

  return [`实体：${entityCount}`, `关系：${relationCount}`].join(' '.repeat(20));
});

// 为未索引节点添加索引
const indexNodes = () => {
  // 判断 embed_model_name 是否相同
  if (!modelMatched.value) {
    message.error(`向量模型不匹配，无法添加索引，当前向量模型为 ${cur_embed_model.value}，图数据库向量模型为 ${graphInfo.value?.embed_model_name}`)
    return
  }

  if (state.precessing) {
    message.error('后台正在处理，请稍后再试')
    return
  }

  state.indexing = true;
  graphApi.indexNodes('neo4j')
    .then(data => {
      message.success(data.message || '索引添加成功');
      // 刷新图谱信息
      loadGraphInfo();
    })
    .catch(error => {
      console.error(error);
      message.error(error.message || '添加索引失败');
    })
    .finally(() => {
      state.indexing = false;
    });
};

const getAuthHeaders = () => {
  const userStore = useUserStore();
  return userStore.getAuthHeaders();
};

</script>

<style lang="less" scoped>
.graph-container {
  padding: 0;
}

.status-wrapper {
  display: flex;
  align-items: center;
  margin-right: 16px;
  font-size: 14px;
  color: rgba(0, 0, 0, 0.65);
}

.status-indicator {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  display: inline-block;

  &.loading {
    background-color: #faad14;
    animation: pulse 1.5s infinite ease-in-out;
  }

  &.open {
    background-color: #52c41a;
  }

  &.closed {
    background-color: #f5222d;
  }
}

@keyframes pulse {
  0% {
    transform: scale(0.8);
    opacity: 0.5;
  }
  50% {
    transform: scale(1.2);
    opacity: 1;
  }
  100% {
    transform: scale(0.8);
    opacity: 0.5;
  }
}

.actions {
  display: flex;
  justify-content: space-between;
  gap: 0px;


  .actions-left {
    display: flex;
    align-items: center;
    gap: 20px;
  }
  .actions-middle {
    display: flex;
    align-items: center;
    gap: 0px;
  }
  .actions-right {
    display: flex;
    align-items: center;
    gap: 0px;
  }
  .actions-left .ant-btn.active {
  background-color: #fa8c16 !important; /* 橙色高亮 */
  border-color: #fa8c16 !important;
  color: #fff !important;
  box-shadow: 0 0 6px rgba(250, 140, 22, 0.5);
}


  .actions-left .ant-btn {
    transition: 0.2s;
  }



  input {
    width: 100px;
    border-radius: 8px;
    padding: 4px 12px;
    border: 2px solid var(--main-300);
    outline: none;
    height: 42px;

    &:focus {
      border-color: var(--main-color);
    }
  }

  button {
    border-width: 2px;
    height: 40px;
    box-shadow: none;
  }
}


.upload {
  margin-bottom: 20px;

  .upload-dragger {
    margin: 0px;
  }
}

#container {
  background: rgb(243, 243, 243);
  margin: 10px 14px;
  border-radius: 16px;
  width: calc(100% - 48px);
  height: calc(100vh - 100px);
  resize: horizontal;
  overflow: hidden;
}

.database-empty {
  display: flex;
  justify-content: center;
  align-items: center;
  width: 100%;
  height: 100%;
  flex-direction: column;
  color: var(--gray-900);
}

.main-content {
  display: flex;
  height: calc(100vh - 120px);
  gap: 20px;
  padding: 10px 14px;
}

/* 左半部分图谱 */
.graph-panel {
  flex: 1;
  background: rgb(243, 243, 243);
  border-radius: 16px;
  overflow: hidden;
}

/* 右半部分控制区 */
.control-panel {
  width: 400px;
  background: #fff;
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);

  h3 {
    margin-bottom: 20px;
    color: var(--main-color);
    text-align: center;
  }

  .control-actions {
    display: flex;
    flex-direction: column;
    gap: 16px;

    input {
      border: 2px solid var(--main-300);
      border-radius: 8px;
      padding: 8px 12px;
    }

    button {
      height: 40px;
    }
  }
}

.uploaded-files,
.downloaded-files {
  background: #fafafa;
  border-radius: 8px;
  padding: 0.8rem;
  margin-top: 1rem;
  box-shadow: inset 0 0 4px rgba(0, 0, 0, 0.05);
  flex-shrink: 0; /* 防止被压缩 */
}

.uploaded-title {
  font-weight: bold;
  margin-bottom: 0.5rem;
  color: #333;
}

.uploaded-list {
  max-height: 200px; /* 内部文件列表的滚动区域 */
  overflow-y: auto;
  background: #fff;
  border-radius: 6px;
  padding: 0.5rem;
  border: 1px solid #eee;
}

.uploaded-list ul {
  list-style: none;
  margin: 0;
  padding: 0;
}

.uploaded-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 8px;
  border-bottom: 1px solid #f0f0f0;
}

.uploaded-list li:last-child {
  border-bottom: none;
}

.file-name {
  flex-grow: 1;
  color: #555;
  word-break: break-all;
}

.file-size {
  color: #666;
  font-size: 12px;
  margin-left: 16px;
  white-space: nowrap;
}

ul {
  list-style: none;
  padding: 0;
  margin: 0;
}

li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px dashed #eee;
}

li:last-child {
  border-bottom: none;
}

.uploaded-list::-webkit-scrollbar {
  width: 6px;
}
.uploaded-list::-webkit-scrollbar-thumb {
  background-color: #ccc;
  border-radius: 3px;
}
.uploaded-list::-webkit-scrollbar-track {
  background: #f5f5f5;
}
.file-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}


</style>
