<template>
  <div class="multimodal-container">
    <a-tabs v-model:activeKey="activeTab" type="card" class="main-tabs">
      
      <a-tab-pane key="manage" tab="📚 知识库管理">
        <div class="kb-manage-layout">
          <div class="kb-sidebar">
            <div class="sidebar-header">
              <span>知识库列表</span>
              <a-button type="primary" size="small" @click="showCreateModal = true">新建</a-button>
            </div>
            <div class="kb-list-wrapper">
              <a-spin :spinning="loadingKbs">
                <div class="kb-list">
                  <div 
                    v-for="kb in kbList" 
                    :key="kb.kbId"
                    class="kb-item" 
                    :class="{ active: currentKbId === kb.kbId }"
                    @click="selectKb(kb)"
                  >
                    <div class="kb-icon">📂</div>
                    <div class="kb-info">
                      <div class="kb-name">{{ kb.kbName }}</div>
                      <div class="kb-meta">{{ kb.vectorStoreType || 'faiss' }} | {{ kb.fileCount || 0 }} 文件</div>
                    </div>
                    <a-popconfirm title="确定删除?" @confirm.stop="handleDeleteKb(kb.kbId)">
                      <delete-outlined class="del-icon" />
                    </a-popconfirm>
                  </div>
                </div>
              </a-spin>
            </div>
          </div>

          <div class="kb-content" v-if="currentKbId">
            <div class="content-header">
              <h3>{{ currentKbName }} <span class="kb-id-tag">ID: {{ currentKbId }}</span></h3>
              <a-upload 
                :customRequest="handleUploadFile" 
                :showUploadList="false" 
                multiple
                accept=".pdf,.xlsx,.xls,.csv"
              >
                <a-button type="primary">📤 上传文件</a-button>
              </a-upload>
            </div>

            <a-tabs v-model:activeKey="detailTab">
              <a-tab-pane key="files" tab="📑 文件列表">
                <div class="toolbar">
                  <span>解析模型: </span>
                  <a-select v-model:value="parseMethod" style="width: 150px; margin-left: 10px;">
                    <a-select-option value="original">基础解析 (OCR)</a-select-option>
                    <a-select-option value="olmocr">增强解析 (多模态)</a-select-option>
                  </a-select>
                  <a-divider type="vertical" />
                  <a-button @click="batchAction('parse')" :loading="batchLoading">🚀 批量解析</a-button>
                  <a-button @click="batchAction('index')" :loading="batchLoading">🚋 建立索引</a-button>
                  <a-button danger @click="batchAction('delete')">🗑️ 删除选中</a-button>
                  <a-button type="link" @click="loadFiles">刷新列表</a-button>
                </div>

                <a-table 
                  :dataSource="fileList" 
                  :columns="fileColumns" 
                  rowKey="fileId"
                  :row-selection="{ selectedRowKeys: selectedFiles, onChange: onSelectFiles }"
                  :pagination="{ pageSize: 10 }"
                  size="middle"
                >
                  <template #bodyCell="{ column, record }">
                    <template v-if="column.key === 'status'">
                      <a-tag :color="record.hasParsed ? 'green' : 'orange'">{{ record.hasParsed ? '已解析' : '未解析' }}</a-tag>
                      <a-tag :color="record.isIndexed ? 'blue' : 'default'">{{ record.isIndexed ? '已索引' : '未索引' }}</a-tag>
                    </template>
                    <template v-if="column.key === 'fileName'">
                      <a @click="previewFile(record)">📄 {{ record.fileName }}</a>
                    </template>
                  </template>
                </a-table>
              </a-tab-pane>

              <a-tab-pane key="images" tab="🖼️ 图片视图">
                <div class="image-toolbar">
                  <a-button :loading="imageLoading" @click="loadImages">🔄 刷新图片</a-button>
                  <a-button type="primary" :loading="savingImageDescs" @click="saveAllImageDescs">💾 保存当前页描述修改</a-button>
                  <span class="image-total">共 {{ imageList.length }} 张，当前只加载 {{ pagedImageList.length }} 张</span>
                </div>
                <div class="image-grid">
                  <a-card v-for="(img, idx) in pagedImageList" :key="getImageKey(img, idx)" hoverable class="img-card">
                    <template #cover>
                      <div class="img-wrapper" @click="previewSingleImage(img)">
                         <img :src="getImgUrl(img)" alt="img" loading="lazy" decoding="async" />
                      </div>
                    </template>
                    <a-card-meta>
                      <template #description>
                        <div class="img-meta-info">P{{ img.page_num }} | {{ img.fileName }}</div>
                        <a-textarea 
                          v-model:value="img.summary" 
                          placeholder="输入图片描述..." 
                          :auto-size="{ minRows: 2, maxRows: 4 }"
                          class="desc-input"
                        />
                      </template>
                    </a-card-meta>
                  </a-card>
                </div>
                <a-empty v-if="!imageList.length" description="暂无图片数据" />
                <a-pagination
                  v-if="imageList.length > imagePageSize"
                  class="image-pagination"
                  :current="imagePage"
                  :page-size="imagePageSize"
                  :total="imageList.length"
                  :page-size-options="imagePageSizeOptions"
                  show-size-changer
                  show-less-items
                  @change="handleImagePageChange"
                  @showSizeChange="handleImagePageChange"
                />
              </a-tab-pane>

              <a-tab-pane key="data" tab="📊 数据表格">
                <div style="display: flex; gap: 20px;">
                   <div style="width: 250px;">
                      <a-list bordered :data-source="excelFiles">
                        <template #renderItem="{ item }">
                          <a-list-item class="clickable-item" @click="loadSheetData(item)">
                            📊 {{ item.fileName }}
                          </a-list-item>
                        </template>
                      </a-list>
                   </div>
                   <div style="flex: 1; overflow-x: auto;">
                      <div v-if="currentSheetNames.length > 1" style="margin-bottom: 10px;">
                        <a-radio-group v-model:value="activeSheet" button-style="solid" size="small">
                          <a-radio-button v-for="s in currentSheetNames" :key="s" :value="s">{{ s }}</a-radio-button>
                        </a-radio-group>
                      </div>
                      <a-table 
                        v-if="currentSheetData.length" 
                        :dataSource="currentSheetData" 
                        :columns="sheetColumns" 
                        size="small" 
                        bordered
                        :scroll="{ x: true }"
                      />
                      <a-empty v-else description="请选择左侧 Excel 文件查看" />
                   </div>
                </div>
              </a-tab-pane>
            </a-tabs>
          </div>
          <div class="kb-content empty-state" v-else>
            <a-empty description="请选择或新建一个知识库" />
          </div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="search" tab="🔍 多模态检索">
        <div class="search-container">
          <section class="search-panel search-condition-panel">
            <div class="search-panel-header">
              <h3>检索条件</h3>
            </div>
            <div class="search-form-grid">
              <label class="search-field search-query-field">
                <span>问题</span>
                <a-input
                  v-model:value="searchQuery"
                  size="large"
                  placeholder="请输入关键词或问题"
                  @pressEnter="handleSearch"
                />
              </label>

              <label class="search-field">
                <span>选择检索知识库</span>
                <a-select
                  v-model:value="searchKbId"
                  show-search
                  placeholder="请选择知识库"
                  :options="kbList.map(kb => ({ label: kb.kbName, value: kb.kbId }))"
                  :filter-option="(input, option) => option.label.toLowerCase().includes(input.toLowerCase())"
                />
              </label>

              <label class="search-field search-range-field">
                <span>选择搜索范围</span>
                <a-select
                  v-model:value="selectedFile"
                  allow-clear
                  show-search
                  :disabled="!searchKbId"
                  placeholder="当前知识库（全部文件）"
                  :options="searchKbFiles.map(f => ({ label: f.fileName || f.fileId, value: f.fileId }))"
                  :filter-option="(input, option) => option.label.toLowerCase().includes(input.toLowerCase())"
                />
              </label>

              <label class="search-field search-topk-field">
                <span>召回数量</span>
                <a-input-number v-model:value="searchTopK" :min="1" :max="20" />
              </label>

              <div class="search-form-actions">
                <a-button type="primary" size="large" :loading="searching" @click="handleSearch">
                  搜索
                </a-button>
              </div>
            </div>
          </section>

          <section class="search-panel search-results-panel">
            <div class="search-panel-header">
              <h3>搜索结果</h3>
              <span class="search-result-count">
                {{ searchResults.length ? `共 ${searchResults.length} 条` : '暂无结果' }}
              </span>
            </div>

            <a-spin :spinning="searching">
              <a-collapse
                v-if="searchResults.length"
                v-model:activeKey="expandedSearchResults"
                class="search-result-collapse"
              >
                <a-collapse-panel
                  v-for="(item, index) in searchResults"
                  :key="getSearchResultKey(item, index)"
                  class="search-result-panel"
                >
                  <template #header>
                    <div class="search-result-summary">
                      <strong>结果 {{ index + 1 }}</strong>
                      <span>得分：{{ formatScore(item.score) }}</span>
                      <span v-if="getSearchResultFileId(item)">
                        文件：{{ getSearchResultFileId(item) }}
                      </span>
                      <span v-if="getSearchResultType(item)">
                        类型：{{ getSearchResultType(item) }}
                      </span>
                    </div>
                  </template>

                  <div
                    class="result-content"
                    v-html="renderMarkdown(item.chunk_text || item.text || item.content, item)"
                    @click="handleSearchResultContentClick"
                  ></div>

                  <footer
                    v-if="getSearchResultSourceRows(item).length"
                    class="search-result-source"
                  >
                    <strong>出处</strong>
                    <div class="search-source-rows">
                      <span
                        v-for="row in getSearchResultSourceRows(item)"
                        :key="`${row.label}-${row.value}`"
                      >
                        <b>{{ row.label }}</b>
                        {{ row.value }}
                      </span>
                    </div>
                  </footer>
                </a-collapse-panel>
              </a-collapse>

              <a-empty
                v-else
                :description="searching ? '正在检索' : '输入问题并点击搜索后，结果会显示在这里'"
              />
            </a-spin>
          </section>
        </div>
      </a-tab-pane>

      <a-tab-pane key="index" tab="🧩 索引管理">
        <div class="index-container">
          <div class="toolbar index-toolbar">
            <a-select
              v-model:value="indexKbId"
              show-search
              placeholder="选择知识库"
              style="width: 240px"
              :options="kbList.map(kb => ({ label: kb.kbName, value: kb.kbId }))"
              :filter-option="(input, option) => option.label.toLowerCase().includes(input.toLowerCase())"
            />
            <a-select
              v-model:value="indexFileId"
              allow-clear
              show-search
              placeholder="可选：按文件筛选"
              style="width: 260px"
              :options="indexFiles.map(file => ({ label: file.fileName, value: file.fileId }))"
              :filter-option="(input, option) => option.label.toLowerCase().includes(input.toLowerCase())"
            />
            <a-input-search
              v-model:value="indexQuery"
              placeholder="搜索索引块内容"
              style="width: 280px"
              @search="loadIndexChunks"
            />
            <a-button :loading="indexLoading" @click="loadIndexDashboard">刷新</a-button>
            <a-popconfirm title="确定删除当前范围的索引吗？" @confirm="deleteCurrentIndex">
              <a-button danger :loading="indexDeleting">删除索引</a-button>
            </a-popconfirm>
          </div>

          <a-row :gutter="16" class="index-stats" v-if="indexStats">
            <a-col :span="6"><a-statistic title="索引块总数" :value="indexStats.total || 0" /></a-col>
            <a-col :span="6"><a-statistic title="文本块" :value="indexStats.text || 0" /></a-col>
            <a-col :span="6"><a-statistic title="图片块" :value="indexStats.image || 0" /></a-col>
            <a-col :span="6"><a-statistic title="文件数" :value="indexStats.files || 0" /></a-col>
          </a-row>

          <a-table
            :dataSource="indexChunks"
            :columns="indexChunkColumns"
            rowKey="rowKey"
            :loading="indexLoading"
            :pagination="false"
            size="small"
            bordered
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'type'">
                <a-tag :color="record.type === 'image' ? 'purple' : 'blue'">{{ record.type || 'text' }}</a-tag>
              </template>
              <template v-if="column.key === 'content'">
                <div class="chunk-preview">{{ record.content }}</div>
              </template>
            </template>
          </a-table>
          <div class="pager-bar">
            <a-button :disabled="indexOffset === 0 || indexLoading" @click="changeIndexPage(-1)">上一页</a-button>
            <span>第 {{ Math.floor(indexOffset / indexLimit) + 1 }} 页 / 共 {{ indexTotal }} 条</span>
            <a-button :disabled="indexOffset + indexLimit >= indexTotal || indexLoading" @click="changeIndexPage(1)">下一页</a-button>
          </div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="structured" tab="🗄️ 结构化数据库">
        <div class="structured-container">
          <a-row :gutter="16">
            <a-col :span="8">
              <div class="panel-block">
                <div class="panel-title">
                  <span>连接管理</span>
                  <a-button size="small" @click="loadStructuredDashboard">刷新</a-button>
                </div>
                <a-form layout="vertical">
                  <a-form-item label="连接类型">
                    <a-select v-model:value="structuredConnectForm.type" :options="structuredSupportedTypes.map(type => ({ label: type, value: type }))" />
                  </a-form-item>
                  <a-form-item label="连接名称">
                    <a-input v-model:value="structuredConnectForm.name" placeholder="例如：生产 MySQL" />
                  </a-form-item>
                  <a-form-item label="SQLite 路径" v-if="structuredConnectForm.type === 'sqlite'">
                    <a-input v-model:value="structuredConnectForm.sqlitePath" placeholder="/data/example.db" />
                  </a-form-item>
                  <template v-else>
                    <a-form-item label="主机">
                      <a-input v-model:value="structuredConnectForm.host" placeholder="127.0.0.1" />
                    </a-form-item>
                    <a-form-item label="端口">
                      <a-input-number v-model:value="structuredConnectForm.port" style="width: 100%" />
                    </a-form-item>
                    <a-form-item label="数据库">
                      <a-input v-model:value="structuredConnectForm.database" />
                    </a-form-item>
                    <a-form-item label="用户名">
                      <a-input v-model:value="structuredConnectForm.username" />
                    </a-form-item>
                    <a-form-item label="密码">
                      <a-input-password v-model:value="structuredConnectForm.password" />
                    </a-form-item>
                  </template>
                  <a-button type="primary" block :loading="structuredLoading" @click="connectStructuredDb">连接</a-button>
                </a-form>
              </div>
            </a-col>

            <a-col :span="16">
              <div class="panel-block">
                <div class="panel-title">
                  <span>已连接数据库</span>
                  <a-select
                    v-model:value="structuredConnectionId"
                    allow-clear
                    placeholder="选择连接"
                    style="width: 260px"
                    :options="structuredConnections.map(conn => ({ label: conn.name || conn.connectionId || conn.id, value: conn.connectionId || conn.id }))"
                  />
                </div>
                <a-list bordered :data-source="structuredConnections" size="small">
                  <template #renderItem="{ item }">
                    <a-list-item>
                      <a-list-item-meta :title="item.name || item.connectionId || item.id" :description="`${item.type || '-'} | ${item.database || item.sqlitePath || item.host || ''}`" />
                      <a-button size="small" danger @click="disconnectStructuredDb(item)">断开</a-button>
                    </a-list-item>
                  </template>
                </a-list>
              </div>

              <div class="panel-block">
                <div class="panel-title">
                  <span>Schema 与表数据</span>
                  <a-button size="small" :disabled="!structuredConnectionId" @click="loadStructuredSchema">加载 Schema</a-button>
                </div>
                <pre v-if="structuredSchema" class="json-panel">{{ JSON.stringify(structuredSchema, null, 2) }}</pre>
                <div class="structured-query-line">
                  <a-input v-model:value="structuredSchemaName" placeholder="schema，可空" style="width: 180px" />
                  <a-input v-model:value="structuredTableName" placeholder="表名" style="width: 220px" />
                  <a-input-number v-model:value="structuredTableLimit" :min="1" :max="500" />
                  <a-button :disabled="!structuredConnectionId || !structuredTableName" @click="previewStructuredTable">预览表</a-button>
                </div>
                <a-table
                  v-if="structuredTableRows.length"
                  :dataSource="structuredTableRows"
                  :columns="structuredTableColumns"
                  rowKey="_rowKey"
                  size="small"
                  bordered
                  :scroll="{ x: true }"
                />
              </div>

              <div class="panel-block">
                <div class="panel-title">SQL 查询</div>
                <a-textarea v-model:value="structuredSql" :rows="4" placeholder="select * from table limit 20" />
                <div class="structured-query-line">
                  <a-input-number v-model:value="structuredQueryLimit" :min="1" :max="500" />
                  <a-button type="primary" :loading="structuredQueryLoading" :disabled="!structuredConnectionId || !structuredSql" @click="runStructuredQuery">运行查询</a-button>
                </div>
                <a-table
                  v-if="structuredQueryRows.length"
                  :dataSource="structuredQueryRows"
                  :columns="structuredQueryColumns"
                  rowKey="_rowKey"
                  size="small"
                  bordered
                  :scroll="{ x: true }"
                />
              </div>
            </a-col>
          </a-row>
        </div>
      </a-tab-pane>

      <a-tab-pane key="preprocess" tab="🧪 数据预处理">
        <div class="preprocess-container">
          <a-row :gutter="16">
            <a-col :span="10">
              <div class="panel-block">
                <div class="panel-title">上传预处理工作台</div>
                <a-upload-dragger
                  :customRequest="handlePreprocessUpload"
                  :showUploadList="true"
                  :fileList="preprocessUploadList"
                  @change="handlePreprocessUploadChange"
                >
                  <p class="ant-upload-text">上传 Excel / CSV / LAS 等数据文件</p>
                </a-upload-dragger>
                <a-alert v-if="preprocessJobId" :message="`当前 Job: ${preprocessJobId}`" type="success" show-icon style="margin-top: 12px" />
                <a-divider />
                <a-checkbox-group v-model:value="selectedPreprocessMethods" class="method-list">
                  <a-checkbox v-for="method in preprocessMethods" :key="method.id" :value="method.id">
                    {{ method.name || method.id }}
                  </a-checkbox>
                </a-checkbox-group>
                <div class="structured-query-line">
                  <a-select
                    v-model:value="preprocessTargetKbId"
                    allow-clear
                    placeholder="保存到已有知识库"
                    style="width: 220px"
                    :options="kbList.map(kb => ({ label: kb.kbName, value: kb.kbId }))"
                  />
                  <a-input v-model:value="preprocessNewKbName" placeholder="或新建知识库名称" style="width: 220px" />
                </div>
                <div class="structured-query-line">
                  <a-checkbox v-model:checked="preprocessSaveToKb">保存入库</a-checkbox>
                  <a-checkbox v-model:checked="preprocessRebuildIndex">重建索引</a-checkbox>
                  <a-button type="primary" :loading="preprocessRunning" @click="runPreprocessWorkbench">运行工作台</a-button>
                  <a-button :disabled="!preprocessJobId" @click="storePreprocessResult">保存结果</a-button>
                </div>
                <div class="structured-query-line">
                  <a-select v-model:value="preprocessDownloadFormat" style="width: 120px">
                    <a-select-option value="xlsx">xlsx</a-select-option>
                    <a-select-option value="csv">csv</a-select-option>
                  </a-select>
                  <a-button :href="getPreprocessDownloadUrl()" target="_blank" :disabled="!preprocessJobId">下载结果</a-button>
                  <a-button :disabled="!preprocessJobId" @click="loadPreprocessWorkbenchDataFrame">预览结果</a-button>
                </div>
              </div>

              <div class="panel-block">
                <div class="panel-title">Grouped Run</div>
                <a-select
                  v-model:value="groupedPreprocessMethod"
                  placeholder="选择 grouped 方法"
                  style="width: 100%; margin-bottom: 10px"
                  :options="preprocessMethods.map(method => ({ label: method.name || method.id, value: method.id }))"
                />
                <a-textarea v-model:value="groupedPreprocessJobsText" :rows="5" placeholder='[{"jobId":"...","role":"raw"}]' />
                <a-button style="margin-top: 10px" :loading="preprocessRunning" @click="runGroupedPreprocess">运行 grouped 预处理</a-button>
              </div>
            </a-col>

            <a-col :span="14">
              <div class="panel-block">
                <div class="panel-title">已有知识库文件预处理</div>
                <div class="structured-query-line">
                  <a-select
                    v-model:value="preprocessKbId"
                    show-search
                    placeholder="知识库"
                    style="width: 220px"
                    :options="kbList.map(kb => ({ label: kb.kbName, value: kb.kbId }))"
                    :filter-option="(input, option) => option.label.toLowerCase().includes(input.toLowerCase())"
                  />
                  <a-select
                    v-model:value="preprocessFileId"
                    show-search
                    placeholder="文件"
                    style="width: 260px"
                    :options="preprocessFiles.map(file => ({ label: file.fileName, value: file.fileId }))"
                    :filter-option="(input, option) => option.label.toLowerCase().includes(input.toLowerCase())"
                  />
                  <a-checkbox v-model:checked="preprocessFileRebuildIndex">重建索引</a-checkbox>
                </div>
                <a-textarea v-model:value="preprocessConfigText" :rows="4" placeholder='{"methods":["format_standardize"]}' />
                <div class="structured-query-line">
                  <a-button type="primary" :loading="preprocessRunning" @click="runKbPreprocess">运行文件预处理</a-button>
                  <a-button :disabled="!preprocessKbId || !preprocessFileId" @click="loadKbPreprocessReport">查看报告</a-button>
                  <a-button :disabled="!preprocessKbId || !preprocessFileId" @click="loadKbPreprocessDataFrame">预览数据</a-button>
                </div>
                <pre v-if="preprocessReport" class="json-panel">{{ JSON.stringify(preprocessReport, null, 2) }}</pre>
              </div>

              <div class="panel-block">
                <div class="panel-title">预处理结果预览</div>
                <a-table
                  v-if="preprocessRows.length"
                  :dataSource="preprocessRows"
                  :columns="preprocessColumns"
                  rowKey="_rowKey"
                  size="small"
                  bordered
                  :scroll="{ x: true }"
                />
                <a-empty v-else description="暂无预览数据" />
              </div>
            </a-col>
          </a-row>
        </div>
      </a-tab-pane>

      <a-tab-pane key="extract" tab="⛏️ 知识提取">
        <div class="extract-container" style="padding: 20px; max-width: 1200px; margin: 0 auto; background: #fff; border-radius: 8px;">
           <div v-if="!extractJobId" class="extract-config-layout">
              <a-alert message="知识提取流: 上传文档 -> 设置提取目标 -> 智能提取 -> 结构化入库" type="info" show-icon style="margin-bottom: 20px;" />
              
              <a-row :gutter="24">
                 <a-col :span="14">
                    <a-card title="1. 文件与配置" :bordered="false" style="background:#fafafa;">
                       <a-form layout="vertical">
                          <a-form-item label="上传文件 (PDF/Excel/CSV/TXT)">
                             <a-upload-dragger 
                                v-model:fileList="extractFileList" 
                                :maxCount="1" 
                                :beforeUpload="() => false"
                                accept=".pdf,.xlsx,.xls,.csv,.txt,.md"
                             >
                                <p class="ant-upload-text">点击或拖拽文件到此区域</p>
                             </a-upload-dragger>
                          </a-form-item>
                          
                          <a-form-item label="提取模板 (可选)">
                             <a-select v-model:value="extractConfig.templateKey" @change="onTemplateChange" placeholder="选择预置模板 (油气领域)">
                                <a-select-option v-for="(v, k) in PRESET_TEMPLATES" :key="k" :value="k">{{ k }}</a-select-option>
                             </a-select>
                          </a-form-item>

                          <a-form-item label="提取指令 (Prompt)" required>
                             <a-textarea v-model:value="extractConfig.instruction" :rows="5" placeholder="例如：请提取文档中的所有发票信息，包含发票代码、号码、金额、日期。..." />
                          </a-form-item>
                          
                          <a-row :gutter="16">
                             <a-col :span="12">
                                <a-form-item label="输出格式">
                                   <a-select v-model:value="extractConfig.outputFmt">
                                      <a-select-option value="Excel">Excel</a-select-option>
                                      <a-select-option value="CSV">CSV</a-select-option>
                                   </a-select>
                                </a-form-item>
                             </a-col>
                             <a-col :span="12">
                                <a-form-item label="解析模型">
                                   <a-select v-model:value="extractConfig.parseMethod">
                                      <a-select-option value="original">基础解析 (OCR)</a-select-option>
                                      <a-select-option value="olmocr">增强解析 (多模态)</a-select-option>
                                   </a-select>
                                </a-form-item>
                             </a-col>
                          </a-row>
                          <a-form-item label="保存文件名">
                             <a-input v-model:value="extractConfig.customFilename" placeholder="可选，留空自动生成" />
                          </a-form-item>
                       </a-form>
                    </a-card>
                 </a-col>
                 <a-col :span="10">
                    <a-card title="2. 目标知识库" :bordered="false" style="background:#fafafa;">
                        <div style="margin-bottom: 16px;">
                            <a-radio-group v-model:value="extractConfig.kbMode" button-style="solid">
                               <a-radio-button value="existing">现有知识库</a-radio-button>
                               <a-radio-button value="new">新建知识库</a-radio-button>
                            </a-radio-group>
                        </div>
                        
                        <div v-if="extractConfig.kbMode === 'existing'">
                           <div style="margin-bottom: 10px;">选择一个知识库用于存储提取结果：</div>
                           <a-select 
                              v-model:value="extractConfig.targetKbId" 
                              style="width: 100%" 
                              placeholder="选择目标知识库" 
                              :options="kbList.map(k => ({ label: k.kbName, value: k.kbId }))"
                              size="large"
                           />
                           <div v-if="!kbList.length" style="color: orange; margin-top:10px;">暂无知识库，请切换到新建模式</div>
                        </div>
                        <div v-else>
                           <!-- Reusing global create form data slightly awkward but effective -->
                           <a-form layout="vertical">
                               <a-form-item label="新知识库名称">
                                  <a-input v-model:value="createForm.kbName" placeholder="如: 提取结果库_2025" />
                               </a-form-item>
                               <a-form-item label="向量库类型">
                                  <a-radio-group v-model:value="createForm.vectorStoreType">
                                    <a-radio value="faiss">FAISS</a-radio>
                                    <a-radio value="milvus">Milvus</a-radio>
                                  </a-radio-group>
                               </a-form-item>
                               <a-form-item>
                                  <a-button type="primary" :loading="creatingKb" @click="handleCreateKbAndSelect">立即创建并选中</a-button>
                               </a-form-item>
                           </a-form>
                        </div>
                    </a-card>
                 </a-col>
              </a-row>
              
              <div style="text-align: center; margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee;">
                 <a-button type="primary" size="large" @click="startExtractionJob" :loading="extracting" style="width: 240px; height: 50px; font-size: 18px;">🚀 开始提取</a-button>
              </div>
           </div>

           <div v-else class="extract-running-layout">
               <div class="status-bar" style="margin-bottom: 24px; padding: 20px; background: #fafafa; border-radius: 8px; border: 1px solid #eee;">
                  <h3 style="margin-bottom: 20px;">任务处理中: {{ extractJobId }}</h3>
                  <a-steps :current="extractStep">
                     <a-step title="上传与初始化" />
                     <a-step title="智能解析" description="OCR与版面分析" />
                     <a-step title="信息提取" description="LLM结构化提取" />
                     <a-step title="完成" />
                  </a-steps>
                  <div style="margin-top: 24px; text-align: center;">
                      <div v-if="extractJobInfo && extractJobInfo.status === 'failed'" style="color: red; margin-bottom: 10px;">
                          ❌ 任务失败: {{ extractJobInfo.error }}
                      </div>
                      <a-tag color="processing" size="large" v-else-if="extractJobInfo && extractJobInfo.status !== 'completed'">
                         当前状态: {{ extractJobInfo.status }} (进度: {{ extractJobInfo.progress }}%)
                      </a-tag>
                      <a-tag color="success" size="large" v-else>✅ 任务完成</a-tag>

                      <div style="margin-top: 16px;">
                          <a-button v-if="extractJobInfo?.status === 'completed' || extractJobInfo?.status === 'failed'" @click="resetExtraction">开启新任务</a-button>
                      </div>
                  </div>
               </div>

               <div v-if="extractJobInfo?.status === 'completed'" class="result-view">
                   <a-tabs type="card" style="min-height: 500px;">
                       <a-tab-pane key="data" tab="📊 提取结果 (可编辑)">
                          <div class="col-action-bar" style="display: flex; gap: 10px; margin-bottom: 10px;">
                             <a-button type="primary" @click="saveExtractionChanges">💾 保存修改并入库</a-button>
                             <a-button :href="getExtractionContentUrl()" target="_blank" v-if="extractJobInfo.result_filepath">⬇️ 查看结果文件</a-button>
                          </div>
                          <a-table 
                             :dataSource="extractResults" 
                             :columns="extractResultColumns" 
                             bordered 
                             :pagination="{ pageSize: 10 }"
                             :scroll="{ x: true }"
                             size="small"
                          >
                             <template #bodyCell="{ column, record }">
                                 <!-- Simple inline edit: just an input -->
                                 <a-input v-model:value="record[column.dataIndex]" style="border: none; background: transparent;" />
                             </template>
                          </a-table>
                       </a-tab-pane>
                       <a-tab-pane key="context" tab="🔍 原文定位">
                          <div style="display: flex; gap: 20px;">
                             <div style="flex: 1;">
                                <h4>文本上下文</h4>
                                <a-textarea :value="extractJobInfo.relevant_context" :rows="20" readonly style="background: #f5f5f5;" />
                             </div>
                             <div style="flex: 1; height: 600px; overflow-y: auto;">
                                <h4>相关页面截图</h4>
                                <div v-if="!extractJobInfo.relevant_pages || !extractJobInfo.relevant_pages.length">无相关页面定位</div>
                                <div v-for="p in extractJobInfo.relevant_pages" :key="p" style="margin-bottom: 20px; border: 1px solid #ddd; padding: 5px;">
                                   <div style="text-align: center; font-weight: bold; margin-bottom: 5px;">Page {{ p }}</div>
                                   <img :src="getExtractPageUrl(p)" style="max-width: 100%; display: block; margin: 0 auto;" />
                                </div>
                             </div>
                          </div>
                       </a-tab-pane>
                   </a-tabs>
               </div>
           </div>
        </div>
      </a-tab-pane>
    </a-tabs>

    <a-modal v-model:open="showCreateModal" title="新建知识库" @ok="handleCreateKb" :confirmLoading="creatingKb">
      <a-form layout="vertical">
        <a-form-item label="知识库名称">
          <a-input v-model:value="createForm.kbName" placeholder="如: 2024财务报表" />
        </a-form-item>
        <a-form-item label="向量库类型">
          <a-radio-group v-model:value="createForm.vectorStoreType">
            <a-radio value="faiss">FAISS (本地)</a-radio>
            <a-radio value="milvus">Milvus</a-radio>
            <a-radio value="es">Elasticsearch</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="Embedding 模型">
          <a-input v-model:value="createForm.embedModel" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 文件预览弹窗 -->
    <a-modal v-model:open="previewVisible" title="📄 文件预览" width="80%" :footer="null">
      <a-spin :spinning="previewLoading">
        <a-tabs v-if="previewFileRecord">
          <a-tab-pane key="md" tab="📝 解析内容">
             <a-textarea :value="previewContent" :rows="20" readonly />
          </a-tab-pane>
          <a-tab-pane key="original" tab="📎 原始文件">
             <a-button type="primary" :href="getOriginalFileUrl()" target="_blank">打开/下载原始文件</a-button>
          </a-tab-pane>
          <a-tab-pane key="fileImages" tab="🖼️ 图片清单">
             <a-list :data-source="previewImages" bordered size="small">
               <template #renderItem="{ item }">
                 <a-list-item>
                   <div class="preview-image-row">
                     <img :src="getPreviewImageUrl(item)" />
                     <div>
                       <div class="kb-name">{{ item.img_name || item.imageName || item.image_path || item.name }}</div>
                       <div class="kb-meta">P{{ item.page_num || item.page || '-' }}</div>
                       <div class="image-summary">{{ getPreviewImageSummary(item) }}</div>
                     </div>
                   </div>
                 </a-list-item>
               </template>
             </a-list>
             <a-empty v-if="!previewImages.length" description="暂无图片清单" />
          </a-tab-pane>
          <a-tab-pane key="pages" tab="🖼️ 页面视图" v-if="previewFileRecord.pageCount > 0">
             <div style="margin-bottom: 10px;">
                <a-radio-group v-model:value="previewPageMode">
                   <a-radio-button value="original">原始页面</a-radio-button>
                   <a-radio-button value="parsed">解析预览 (检测框)</a-radio-button>
                </a-radio-group>
             </div>
             <div style="height: 600px; overflow-y: auto;">
                <div v-for="p in previewFileRecord.pageCount" :key="p" style="margin-bottom: 20px; text-align: center;">
                   <div>Page {{ p }}</div>
                   <img :src="getPreviewPageUrl(p)" style="max-width: 100%; border: 1px solid #eee;" loading="lazy" />
                </div>
             </div>
          </a-tab-pane>
        </a-tabs>
      </a-spin>
    </a-modal>

    <!-- 图片放大预览 -->
    <a-modal v-model:open="singleImagePreviewVisible" :footer="null" centered width="80%" :bodyStyle="{ padding: 0 }">
        <div style="display: flex; justify-content: center; align-items: center; min-height: 400px; background: rgba(0,0,0,0.8); padding: 20px;">
            <img :src="singleImagePreviewUrl" style="max-width: 100%; max-height: 85vh; object-fit: contain;" />
        </div>
    </a-modal>

  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue';
import { message } from 'ant-design-vue';
import { DeleteOutlined } from '@ant-design/icons-vue';
import { marked } from 'marked'; // 需确保已安装 marked
import * as api from '@/apis/multimodal';
import { clampPage, paginateItems } from '@/utils/pagination.mjs';
import {
  getSearchResultFileId,
  getSearchResultSourceRows,
  getSearchResultType,
} from '@/utils/multimodalSearch.mjs';

// 全局状态
const activeTab = ref('manage');
const kbList = ref([]);
const loadingKbs = ref(false);

// ================= KB 管理逻辑 =================
const showCreateModal = ref(false);
const creatingKb = ref(false);
const createForm = reactive({ kbName: '', vectorStoreType: 'faiss', embedModel: 'bge-m3:latest' });
const currentKbId = ref(null);
const currentKbName = ref('');
const detailTab = ref('files');

// 文件列表相关
const fileList = ref([]);
const selectedFiles = ref([]);
const parseMethod = ref('original');
const batchLoading = ref(false);
const fileColumns = [
  { title: '文件名', dataIndex: 'fileName', key: 'fileName' },
  { title: '类型', dataIndex: 'type' },
  { title: '状态', key: 'status' },
];

// 图片管理相关
const imageList = ref([]);
const imageLoading = ref(false);
const savingImageDescs = ref(false);
const imagePage = ref(1);
const imagePageSize = ref(24);
const imagePageSizeOptions = ['12', '24', '48'];
const pagedImageList = computed(() => paginateItems(imageList.value, imagePage.value, imagePageSize.value));

// 数值表格相关
const currentSheetData = ref([]);
const sheetColumns = ref([]);

// 初始化加载
const loadKbList = async () => {
  loadingKbs.value = true;
  try {
    const res = await api.getKbList();
    kbList.value = res.kbs || [];
  } finally {
    loadingKbs.value = false;
  }
};

const handleCreateKb = async () => {
  if (!createForm.kbName) return message.warning('请输入名称');
  creatingKb.value = true;
  try {
    await api.createKb(createForm);
    message.success('创建成功');
    showCreateModal.value = false;
    loadKbList();
  } catch(e) { message.error('创建失败'); } 
  finally { creatingKb.value = false; }
};

const handleDeleteKb = async (id) => {
  try {
    await api.deleteKb({ kbId: id });
    message.success('已删除');
    if (currentKbId.value === id) {
      currentKbId.value = null;
    }
    loadKbList();
  } catch(e) { message.error('删除失败'); }
};

const selectKb = (kb) => {
  currentKbId.value = kb.kbId;
  currentKbName.value = kb.kbName;
  imageList.value = [];
  imagePage.value = 1;
  loadFiles();
  // 懒加载其他 Tab 数据
  if (detailTab.value === 'images') loadImages();
};

const loadFiles = async () => {
  if (!currentKbId.value) return;
  const res = await api.getKbFiles({ kbId: currentKbId.value });
  fileList.value = res.files || [];
};

const handleUploadFile = async ({ file }) => {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('kbId', currentKbId.value);
  try {
    await api.uploadFile(fd);
    message.success(`${file.name} 上传成功`);
    loadFiles();
  } catch(e) { message.error('上传失败'); }
};

// 批量操作
const onSelectFiles = (keys) => { selectedFiles.value = keys; };
const batchAction = async (type) => {
  if (!selectedFiles.value.length) return message.warning('请先勾选文件');
  batchLoading.value = true;
  try {
    for (const fid of selectedFiles.value) {
      if (type === 'parse') await api.parseFile({ kbId: currentKbId.value, fileId: fid, method: parseMethod.value });
      else if (type === 'index') await api.buildIndex({ kbId: currentKbId.value, fileId: fid });
      else if (type === 'delete') await api.deleteKbFile({ kbId: currentKbId.value, fileId: fid, deleteFile: true, deleteIndex: true });
    }
    message.success('批量操作请求已发送');
    // 如果是解析，建议轮询，这里简化为延迟刷新
    setTimeout(loadFiles, 2000);
  } finally {
    batchLoading.value = false;
  }
};

// 图片加载
const loadImages = async ({ keepPage = false } = {}) => {
  if (!currentKbId.value) return;
  const previousPage = imagePage.value;
  imageLoading.value = true;
  try {
    const res = await api.getKbImages({ kbId: currentKbId.value });
    imageList.value = res.images || [];
    imagePage.value = keepPage
      ? clampPage(previousPage, imageList.value.length, imagePageSize.value)
      : 1;
  } finally {
    imageLoading.value = false;
  }
};

const handleImagePageChange = (page, pageSize) => {
  imagePageSize.value = Number(pageSize) || imagePageSize.value;
  imagePage.value = clampPage(page, imageList.value.length, imagePageSize.value);
};

// 构建图片 URL 的统一方法
const getImgUrl = (img) => {
  return api.getPdfImageUrl({
    kbId: currentKbId.value,
    fileId: img.fileId,
    imagePath: img.img_name || img.imagePath || img.path,
  });
};
const getImageKey = (img, idx) => {
  return [
    img.fileId || img.file_id || img.fileName || 'file',
    img.img_name || img.imageName || img.image_path || img.path || idx,
  ].join('::');
};
const saveAllImageDescs = async () => {
  if (!currentKbId.value) return message.warning('请先选择知识库');
  if (!pagedImageList.value.length) return message.info('当前页暂无图片描述可保存');

  const grouped = pagedImageList.value.reduce((acc, img) => {
    const fileId = img.fileId || img.file_id || img.fileName;
    const imgName = img.img_name || img.imageName || img.image_path || img.name;
    if (!fileId || !imgName) return acc;

    if (!acc[fileId]) acc[fileId] = [];
    acc[fileId].push({
      ...img,
      img_name: imgName,
      summary: img.summary || img.description || '',
    });
    return acc;
  }, {});

  const entries = Object.entries(grouped);
  if (!entries.length) return message.warning('图片数据缺少 fileId 或图片名，无法保存');

  savingImageDescs.value = true;
  try {
    await Promise.all(entries.map(([fileId, summaries]) => api.updateImageSummaries({
      kbId: currentKbId.value,
      fileId,
      summaries,
    })));
    message.success('图片描述已保存');
    await loadImages({ keepPage: true });
  } catch (e) {
    message.error(e.message || '图片描述保存失败');
  } finally {
    savingImageDescs.value = false;
  }
};

// 单图预览
const singleImagePreviewVisible = ref(false);
const singleImagePreviewUrl = ref('');
const previewSingleImage = (img) => {
  singleImagePreviewUrl.value = getImgUrl(img);
  singleImagePreviewVisible.value = true;
};

// Excel 数据加载
const excelFiles = computed(() => fileList.value.filter(f => f.type === 'excel'));
const currentSheetNames = ref([]);
const activeSheet = ref('');
const loadSheetData = async (file) => {
  const res = await api.getFileDataFrame({ kbId: currentKbId.value, fileId: file.fileId });
  const sheets = res.sheets || {};
  currentAllSheets.value = sheets;
  currentSheetNames.value = Object.keys(sheets);
  if (currentSheetNames.value.length > 0) {
    activeSheet.value = currentSheetNames.value[0];
    currentSheetData.value = sheets[activeSheet.value];
    // 动态生成列
    if (sheets[activeSheet.value].length > 0) {
      sheetColumns.value = Object.keys(sheets[activeSheet.value][0]).map(k => ({ title: k, dataIndex: k }));
    }
  }
};
// 切换 sheet
const changeSheet = (sheetName) => {
  activeSheet.value = sheetName;
  // 假设当前正在查看的文件数据已缓存在 currentSheetData 对应的上下文中，这里简化处理
  // 实际上需要知道当前查看的是哪个文件。
  // 简单方案: loadSheetData 时把 sheets 挂载到 status 或 ref
  // 这里我们假设 sheets 数据就在 file._sheetsData (但在 Vue 中最好用响应式变量)
  // 修正：我们应该有一个 currentAllSheets 变量
};
const currentAllSheets = ref({});
watch(activeSheet, (val) => {
  if (currentAllSheets.value[val]) {
    currentSheetData.value = currentAllSheets.value[val];
    if (currentSheetData.value.length > 0) {
      sheetColumns.value = Object.keys(currentSheetData.value[0]).map(k => ({ title: k, dataIndex: k }));
    } else {
      sheetColumns.value = [];
    }
  }
});

// ================= 文件预览逻辑 (PDF/Markdown) =================
const previewVisible = ref(false);
const previewLoading = ref(false);
const previewContent = ref('');
const previewFileRecord = ref(null);
const previewPageMode = ref('original'); // 'original' | 'parsed'
const previewImages = ref([]);
const previewImageSummaries = ref([]);

const previewFile = async (record) => {
  previewFileRecord.value = record;
  previewVisible.value = true;
  previewLoading.value = true;
  previewContent.value = '';
  previewImages.value = [];
  previewImageSummaries.value = [];
  
  try {
    const res = await api.getFileContent({ kbId: currentKbId.value, fileId: record.fileId });
    previewContent.value = res.content || '暂无内容';
    const [imagesRes, summariesRes] = await Promise.allSettled([
      api.getPdfImagesList({ kbId: currentKbId.value, fileId: record.fileId }),
      api.getPdfImageSummaries({ kbId: currentKbId.value, fileId: record.fileId }),
    ]);
    if (imagesRes.status === 'fulfilled') {
      previewImages.value = imagesRes.value.images || imagesRes.value.data || [];
    }
    if (summariesRes.status === 'fulfilled') {
      previewImageSummaries.value = summariesRes.value.summaries || summariesRes.value.images || summariesRes.value.data || [];
    }
  } catch (e) {
    previewContent.value = '加载失败: ' + e.message;
  } finally {
    previewLoading.value = false;
  }
};
const getPreviewPageUrl = (page) => {
  if (!previewFileRecord.value) return '';
  return api.getPdfPageUrl({
    kbId: currentKbId.value,
    fileId: previewFileRecord.value.fileId,
    page,
    type: previewPageMode.value,
  });
};

const getOriginalFileUrl = () => {
  if (!previewFileRecord.value) return '#';
  return api.getFileOriginalUrl({
    kbId: currentKbId.value,
    fileId: previewFileRecord.value.fileId,
  });
};

const getPreviewImageUrl = (img) => {
  if (!previewFileRecord.value) return '#';
  return api.getPdfImageUrl({
    kbId: currentKbId.value,
    fileId: previewFileRecord.value.fileId,
    imagePath: img.img_name || img.imageName || img.image_path || img.name,
  });
};

const getPreviewImageSummary = (img) => {
  const imageName = img.img_name || img.imageName || img.image_path || img.name;
  const found = previewImageSummaries.value.find(item => (
    item.img_name || item.imageName || item.image_path || item.name
  ) === imageName);
  return found?.summary || found?.description || img.summary || img.description || '暂无摘要';
};


// ================= 搜索逻辑 =================
const searchKbId = ref(undefined);
const searchQuery = ref('');
const searchTopK = ref(3);
const searchResults = ref([]);
const searching = ref(false);
const selectedFile = ref(null); // 选中的文件ID
const searchKbFiles = ref([]);  // 用于搜索下拉框的文件列表
const expandedSearchResults = ref([]);

const formatScore = (score) => {
  return Number.isFinite(Number(score)) ? Number(score).toFixed(4) : '-';
};

const getSearchResultKey = (item, index) => {
  return String(item?.id ?? item?.citation_id ?? `${getSearchResultFileId(item)}-${index}`);
};

// ★★★ 新增：监听知识库选择变化，自动加载文件列表 ★★★
watch(searchKbId, async (newKbId) => {
  if (newKbId) {
    try {
      // 调用之前定义好的 getKbFiles 接口
      const res = await api.getKbFiles({ kbId: newKbId });
      // 假设后端返回结构是 { files: [...] }
      searchKbFiles.value = res.files || [];
      // 切换知识库后，重置文件选择
      selectedFile.value = null;
    } catch (e) {
      console.error("加载文件列表失败", e);
      searchKbFiles.value = [];
    }
  } else {
    searchKbFiles.value = [];
  }
});

// ★★★ 修改：handleSearch 函数 ★★★
const handleSearch = async () => {
  if (!searchKbId.value || !searchQuery.value) {
    return message.warning('请选择知识库并输入问题');
  }

  searching.value = true;
  try {
    // 构造请求参数
    const searchPayload = {
      kbId: searchKbId.value,
      query: searchQuery.value,
      k: searchTopK.value,
      fileId: selectedFile.value || null,
    };

    const res = await api.searchKb(searchPayload);
    
    // 兼容后端返回
    searchResults.value = res.results || res.data || [];
    expandedSearchResults.value = searchResults.value.length
      ? [getSearchResultKey(searchResults.value[0], 0)]
      : [];
    
    if (searchResults.value.length === 0) {
      message.info('未找到相关内容');
    }
  } catch(e) { 
    console.error(e);
    if (e.response && e.response.status === 422) {
      message.error('参数格式错误 (422)，请检查参数');
    } else {
      message.error('搜索出错: ' + (e.message || '未知错误')); 
    }
  } finally { 
    searching.value = false; 
  }
};
// Markdown 渲染中的图片地址处理
const renderMarkdown = (text, item) => {
  if (!text) return '';
  
  const fileId = getSearchResultFileId(item);

  // 1. 预处理：直接将 Markdown 语法的图片链接替换为后端 API 地址
  // 解决文件名包含空格导致 marked 无法正确解析的问题
  let processedText = text.replace(/!\[([^\]]*)\]\((.*?)\)/g, (match, alt, content) => {
      const trimmedContent = content.trim();
      
      // 模仿 Python os.path.basename: 只取文件名，忽略路径前缀 (如 images/, ./images/)
      const imageName = trimmedContent.split(/[/\\]/).pop();
      if (!imageName) return match;

      // 先解码以防已经是编码过的，确保存储的是原始文件名
      let rawName = imageName;
      try { rawName = decodeURIComponent(imageName); } catch(e) {}
      
      // 再次编码作为 URL 参数
      // 构建 URL: 必须包含 fileId 才能定位到具体文件下的图片
      const newUrl = api.getPdfImageUrl({
        kbId: searchKbId.value,
        fileId,
        imagePath: rawName,
      });

      return `![${alt}](${newUrl})`;
  });

  let html = marked.parse(processedText);
  
  // 2. 兜底处理：处理 HTML <img> 标签中的 images/ 路径
  html = html.replace(
    /src="(\.?\/)?images\/([^"]*?)"/g, 
    (match, prefix, imagePath) => {
      // 同样只提取文件名
      const imgName = imagePath.split(/[/\\]/).pop();
      let rawName = imgName;
      try { rawName = decodeURIComponent(imgName); } catch(e) {}
      const newUrl = api.getPdfImageUrl({
        kbId: searchKbId.value,
        fileId,
        imagePath: rawName,
      });
      return `src="${newUrl}"`;
    }
  );
  html = html.replace(/<img\b(?![^>]*\bloading=)/gi, '<img loading="lazy" decoding="async"');
  return html;
};

const handleSearchResultContentClick = (event) => {
  const image = event.target?.closest?.('img');
  if (!image) return;
  singleImagePreviewUrl.value = image.currentSrc || image.src;
  singleImagePreviewVisible.value = true;
};

watch(detailTab, (val) => {
  if (val === 'images' && currentKbId.value && !imageList.value.length && !imageLoading.value) {
    loadImages();
  }
});

// ================= Index Management Logic =================
const indexKbId = ref(undefined);
const indexFileId = ref(undefined);
const indexFiles = ref([]);
const indexQuery = ref('');
const indexLimit = ref(50);
const indexOffset = ref(0);
const indexTotal = ref(0);
const indexStats = ref(null);
const indexChunks = ref([]);
const indexLoading = ref(false);
const indexDeleting = ref(false);

const indexChunkColumns = [
  { title: '文件', dataIndex: 'fileName', key: 'fileName', width: 220 },
  { title: '类型', dataIndex: 'type', key: 'type', width: 90 },
  { title: '页码', dataIndex: 'page', key: 'page', width: 90 },
  { title: '内容预览', dataIndex: 'content', key: 'content' },
];

const parseMetadata = (item) => {
  const source = item?.source || item?.metadata;
  if (!source) return {};
  if (typeof source === 'object') return source;
  try {
    const parsed = JSON.parse(source);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (e) {
    return {};
  }
};

const normalizeIndexChunk = (item, idx) => {
  const meta = parseMetadata(item);
  const content = item.chunk_text || item.text || item.content || item.snippet || '';
  const fileId = item.fileId || item.file_id || meta.file_id || item.entity_key || '';
  return {
    rowKey: item.id || `${fileId}-${indexOffset.value + idx}`,
    fileId,
    fileName: item.fileName || item.file_name || meta.fileName || meta.file_name || meta.filename || fileId || '-',
    type: item.type || meta.type || (meta.image_path ? 'image' : 'text'),
    page: item.page || item.page_number || meta.page || meta.page_number || '-',
    content,
    raw: item,
  };
};

const loadIndexFiles = async () => {
  if (!indexKbId.value) {
    indexFiles.value = [];
    return;
  }
  const res = await api.getKbFiles({ kbId: indexKbId.value });
  indexFiles.value = res.files || [];
};

const loadIndexStats = async () => {
  if (!indexKbId.value) {
    indexStats.value = null;
    return;
  }
  indexStats.value = await api.getIndexChunksStats({ kbId: indexKbId.value });
};

const loadIndexChunks = async () => {
  if (!indexKbId.value) return message.warning('请选择知识库');
  indexLoading.value = true;
  try {
    const res = await api.getIndexChunks({
      kbId: indexKbId.value,
      fileId: indexFileId.value,
      q: indexQuery.value,
      limit: indexLimit.value,
      offset: indexOffset.value,
    });
    indexTotal.value = res.total || 0;
    indexChunks.value = (res.chunks || res.results || []).map(normalizeIndexChunk);
  } catch (e) {
    message.error(e.message || '索引块加载失败');
  } finally {
    indexLoading.value = false;
  }
};

const loadIndexDashboard = async () => {
  if (!indexKbId.value) return message.warning('请选择知识库');
  indexLoading.value = true;
  try {
    await Promise.all([loadIndexFiles(), loadIndexStats()]);
    await loadIndexChunks();
  } finally {
    indexLoading.value = false;
  }
};

const changeIndexPage = async (direction) => {
  const nextOffset = Math.max(0, indexOffset.value + direction * indexLimit.value);
  if (nextOffset === indexOffset.value) return;
  indexOffset.value = nextOffset;
  await loadIndexChunks();
};

const deleteCurrentIndex = async () => {
  if (!indexKbId.value) return message.warning('请选择知识库');
  indexDeleting.value = true;
  try {
    const payload = { kbId: indexKbId.value };
    if (indexFileId.value) payload.fileId = indexFileId.value;
    await api.deleteIndex(payload);
    message.success(indexFileId.value ? '已删除该文件索引' : '已删除该知识库索引');
    indexOffset.value = 0;
    await loadIndexDashboard();
  } catch (e) {
    message.error(e.message || '索引删除失败');
  } finally {
    indexDeleting.value = false;
  }
};

watch(indexKbId, async (newKbId) => {
  indexFileId.value = undefined;
  indexOffset.value = 0;
  indexStats.value = null;
  indexChunks.value = [];
  if (newKbId) {
    await loadIndexDashboard();
  }
});

watch(indexFileId, async () => {
  indexOffset.value = 0;
  if (indexKbId.value) await loadIndexChunks();
});

// ================= Structured DB Logic =================
const structuredSupportedTypes = ref([]);
const structuredConnections = ref([]);
const structuredConnectionId = ref(undefined);
const structuredLoading = ref(false);
const structuredQueryLoading = ref(false);
const structuredSchema = ref(null);
const structuredSchemaName = ref('');
const structuredTableName = ref('');
const structuredTableLimit = ref(100);
const structuredTableRows = ref([]);
const structuredTableColumns = ref([]);
const structuredSql = ref('');
const structuredQueryLimit = ref(100);
const structuredQueryRows = ref([]);
const structuredQueryColumns = ref([]);

const structuredConnectForm = reactive({
  name: '',
  type: 'sqlite',
  host: '',
  port: null,
  database: '',
  username: '',
  password: '',
  sqlitePath: '',
});

const compactPayload = (payload) => Object.fromEntries(
  Object.entries(payload).filter(([, value]) => value !== '' && value !== null && value !== undefined)
);

const normalizeTabularData = (payload) => {
  const rows = payload?.rows || payload?.data || payload?.records || payload?.result || [];
  const normalizedRows = Array.isArray(rows)
    ? rows.map((row, index) => {
        if (Array.isArray(row)) {
          const columns = payload?.columns || row.map((_, idx) => `col_${idx + 1}`);
          return {
            _rowKey: index,
            ...Object.fromEntries(columns.map((column, idx) => [column, row[idx]])),
          };
        }
        return { _rowKey: index, ...(row || {}) };
      })
    : [];

  const columns = (payload?.columns || Object.keys(normalizedRows[0] || {}).filter(key => key !== '_rowKey')).map(column => ({
    title: column,
    dataIndex: column,
    key: column,
    ellipsis: true,
  }));

  return { rows: normalizedRows, columns };
};

const loadStructuredDashboard = async () => {
  structuredLoading.value = true;
  try {
    const [supported, connections] = await Promise.all([
      api.getStructuredDbSupported(),
      api.getStructuredDbConnections(),
    ]);
    structuredSupportedTypes.value = supported.types || [];
    if (!structuredSupportedTypes.value.includes(structuredConnectForm.type)) {
      structuredConnectForm.type = structuredSupportedTypes.value[0] || 'sqlite';
    }
    structuredConnections.value = connections.connections || [];
    if (!structuredConnectionId.value && structuredConnections.value.length) {
      const first = structuredConnections.value[0];
      structuredConnectionId.value = first.connectionId || first.id;
    }
  } catch (e) {
    message.error(e.message || '结构化数据库连接信息加载失败');
  } finally {
    structuredLoading.value = false;
  }
};

const connectStructuredDb = async () => {
  if (!structuredConnectForm.type) return message.warning('请选择连接类型');
  structuredLoading.value = true;
  try {
    const res = await api.connectStructuredDb(compactPayload({ ...structuredConnectForm }));
    message.success('数据库连接成功');
    await loadStructuredDashboard();
    structuredConnectionId.value = res.connectionId || res.id || structuredConnectionId.value;
  } catch (e) {
    message.error(e.message || '数据库连接失败');
  } finally {
    structuredLoading.value = false;
  }
};

const disconnectStructuredDb = async (item) => {
  const connectionId = item.connectionId || item.id;
  if (!connectionId) return;
  try {
    await api.disconnectStructuredDb({ connectionId });
    message.success('连接已断开');
    if (structuredConnectionId.value === connectionId) structuredConnectionId.value = undefined;
    await loadStructuredDashboard();
  } catch (e) {
    message.error(e.message || '断开连接失败');
  }
};

const loadStructuredSchema = async () => {
  if (!structuredConnectionId.value) return message.warning('请选择连接');
  try {
    structuredSchema.value = await api.getStructuredDbSchema({ connectionId: structuredConnectionId.value });
  } catch (e) {
    message.error(e.message || 'Schema 加载失败');
  }
};

const previewStructuredTable = async () => {
  if (!structuredConnectionId.value || !structuredTableName.value) return message.warning('请选择连接并输入表名');
  try {
    const res = await api.getStructuredDbTable({
      connectionId: structuredConnectionId.value,
      schema: structuredSchemaName.value,
      table: structuredTableName.value,
      limit: structuredTableLimit.value,
      offset: 0,
    });
    const normalized = normalizeTabularData(res);
    structuredTableRows.value = normalized.rows;
    structuredTableColumns.value = normalized.columns;
  } catch (e) {
    message.error(e.message || '表数据预览失败');
  }
};

const runStructuredQuery = async () => {
  if (!structuredConnectionId.value || !structuredSql.value) return message.warning('请选择连接并输入 SQL');
  structuredQueryLoading.value = true;
  try {
    const res = await api.queryStructuredDb({
      connectionId: structuredConnectionId.value,
      sql: structuredSql.value,
      limit: structuredQueryLimit.value,
    });
    const normalized = normalizeTabularData(res);
    structuredQueryRows.value = normalized.rows;
    structuredQueryColumns.value = normalized.columns;
  } catch (e) {
    message.error(e.message || 'SQL 查询失败');
  } finally {
    structuredQueryLoading.value = false;
  }
};

// ================= Preprocess Logic =================
const preprocessMethods = ref([]);
const selectedPreprocessMethods = ref([]);
const preprocessUploadList = ref([]);
const preprocessJobId = ref(null);
const preprocessRunning = ref(false);
const preprocessTargetKbId = ref(undefined);
const preprocessNewKbName = ref('');
const preprocessSaveToKb = ref(true);
const preprocessRebuildIndex = ref(false);
const preprocessDownloadFormat = ref('xlsx');
const groupedPreprocessMethod = ref(undefined);
const groupedPreprocessJobsText = ref('[]');
const preprocessKbId = ref(undefined);
const preprocessFileId = ref(undefined);
const preprocessFiles = ref([]);
const preprocessFileRebuildIndex = ref(false);
const preprocessConfigText = ref('{}');
const preprocessReport = ref(null);
const preprocessRows = ref([]);
const preprocessColumns = ref([]);

const parseJsonText = (text, fallback) => {
  const raw = (text || '').trim();
  if (!raw) return fallback;
  try {
    return JSON.parse(raw);
  } catch (e) {
    message.error('JSON 格式不正确');
    throw e;
  }
};

const loadPreprocessMethods = async () => {
  try {
    const res = await api.getPreprocessMethods();
    preprocessMethods.value = res.methods || [];
    if (!selectedPreprocessMethods.value.length && preprocessMethods.value.length) {
      selectedPreprocessMethods.value = [preprocessMethods.value[0].id];
    }
  } catch (e) {
    message.error(e.message || '预处理方法加载失败');
  }
};

const handlePreprocessUpload = async ({ file, onSuccess, onError }) => {
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await api.uploadPreprocessFile(fd);
    preprocessJobId.value = res.jobId || res.job_id || res.id;
    message.success('文件已上传到预处理工作台');
    onSuccess?.(res);
  } catch (e) {
    message.error(e.message || '预处理文件上传失败');
    onError?.(e);
  }
};

const handlePreprocessUploadChange = ({ fileList }) => {
  preprocessUploadList.value = fileList;
};

const runPreprocessWorkbench = async () => {
  if (!preprocessJobId.value) return message.warning('请先上传文件');
  if (!selectedPreprocessMethods.value.length) return message.warning('请选择预处理方法');
  preprocessRunning.value = true;
  try {
    const res = await api.runPreprocessWorkbench({
      jobId: preprocessJobId.value,
      methods: selectedPreprocessMethods.value,
      targetKbId: preprocessTargetKbId.value,
      newKbName: preprocessNewKbName.value,
      saveToKb: preprocessSaveToKb.value,
      rebuildIndex: preprocessRebuildIndex.value,
    });
    preprocessJobId.value = res.jobId || res.job_id || preprocessJobId.value;
    message.success('预处理工作台任务已完成');
    await loadPreprocessWorkbenchDataFrame();
  } catch (e) {
    message.error(e.message || '预处理工作台运行失败');
  } finally {
    preprocessRunning.value = false;
  }
};

const storePreprocessResult = async () => {
  if (!preprocessJobId.value) return message.warning('没有可保存的预处理任务');
  try {
    await api.storePreprocessWorkbench({
      jobId: preprocessJobId.value,
      targetKbId: preprocessTargetKbId.value,
      newKbName: preprocessNewKbName.value,
      rebuildIndex: preprocessRebuildIndex.value,
    });
    message.success('预处理结果已保存');
  } catch (e) {
    message.error(e.message || '预处理结果保存失败');
  }
};

const loadPreprocessWorkbenchDataFrame = async () => {
  if (!preprocessJobId.value) return message.warning('没有可预览的预处理任务');
  try {
    const res = await api.getPreprocessWorkbenchDataFrame({ jobId: preprocessJobId.value, limit: 500 });
    const normalized = normalizeTabularData(res);
    preprocessRows.value = normalized.rows;
    preprocessColumns.value = normalized.columns;
  } catch (e) {
    message.error(e.message || '预处理结果预览失败');
  }
};

const getPreprocessDownloadUrl = () => {
  if (!preprocessJobId.value) return '#';
  return api.getPreprocessWorkbenchDownloadUrl({
    jobId: preprocessJobId.value,
    format: preprocessDownloadFormat.value,
  });
};

const runGroupedPreprocess = async () => {
  const jobs = parseJsonText(groupedPreprocessJobsText.value, []);
  const method = groupedPreprocessMethod.value || selectedPreprocessMethods.value[0];
  if (!method) return message.warning('请选择 grouped 方法');
  if (!Array.isArray(jobs) || !jobs.length) return message.warning('请输入 jobs 数组');
  preprocessRunning.value = true;
  try {
    const res = await api.runGroupedPreprocessWorkbench({ method, jobs, config: {} });
    preprocessJobId.value = res.jobId || res.job_id || res.id || preprocessJobId.value;
    message.success('Grouped 预处理已完成');
    if (preprocessJobId.value) await loadPreprocessWorkbenchDataFrame();
  } catch (e) {
    message.error(e.message || 'Grouped 预处理失败');
  } finally {
    preprocessRunning.value = false;
  }
};

const loadPreprocessFiles = async () => {
  if (!preprocessKbId.value) {
    preprocessFiles.value = [];
    return;
  }
  try {
    const res = await api.getKbFiles({ kbId: preprocessKbId.value });
    preprocessFiles.value = res.files || [];
    preprocessFileId.value = undefined;
  } catch (e) {
    preprocessFiles.value = [];
    message.error(e.message || '预处理文件列表加载失败');
  }
};

const runKbPreprocess = async () => {
  if (!preprocessKbId.value || !preprocessFileId.value) return message.warning('请选择知识库和文件');
  preprocessRunning.value = true;
  try {
    const config = parseJsonText(preprocessConfigText.value, {});
    await api.runPreprocess({
      kbId: preprocessKbId.value,
      fileId: preprocessFileId.value,
      config,
      rebuildIndex: preprocessFileRebuildIndex.value,
    });
    message.success('知识库文件预处理已完成');
    await loadKbPreprocessDataFrame();
  } catch (e) {
    message.error(e.message || '知识库文件预处理失败');
  } finally {
    preprocessRunning.value = false;
  }
};

const loadKbPreprocessReport = async () => {
  if (!preprocessKbId.value || !preprocessFileId.value) return message.warning('请选择知识库和文件');
  try {
    preprocessReport.value = await api.getPreprocessReport({
      kbId: preprocessKbId.value,
      fileId: preprocessFileId.value,
    });
  } catch (e) {
    message.error(e.message || '预处理报告加载失败');
  }
};

const loadKbPreprocessDataFrame = async () => {
  if (!preprocessKbId.value || !preprocessFileId.value) return message.warning('请选择知识库和文件');
  try {
    const res = await api.getPreprocessDataFrame({
      kbId: preprocessKbId.value,
      fileId: preprocessFileId.value,
      limit: 500,
    });
    const normalized = normalizeTabularData(res);
    preprocessRows.value = normalized.rows;
    preprocessColumns.value = normalized.columns;
  } catch (e) {
    message.error(e.message || '预处理数据加载失败');
  }
};

watch(preprocessKbId, loadPreprocessFiles);

// ================= Knowledge Extraction Logic =================

const PRESET_TEMPLATES = {
    "地层压力和温度": "请提取'地层压力和温度'表格。该表通常包含多级表头。\n目标列(JSON Key)：\n- 序号\n- 井号\n- 原始_饱和压力_MPa\n- 原始_地层压力_MPa\n- 原始_压力系数\n- 原始_油层温度_℃\n- 原始_地温梯度_℃/100m\n- 结论_温度\n- 结论_压力\n- 备注\n注意：请处理'原始'和'结论'下的合并单元格结构，将子列的数据准确提取到对应字段。",
    "油水关系及油藏类型": "请提取'油水关系及油藏类型'表格。\n目标列(JSON Key)：\n- 序号\n- 层位\n- 油藏类型\n- 油藏类型细分\n- 边底水\n- 气顶\n- 油水界面_m\n- 备注\n注意：若存在合并行，请将合并内容填充到每一行。",
    "油分析": "请提取'原油分析'或'油分析'表格。\n目标列(JSON Key)：\n- 序号\n- 层位\n- 取样_取样井号\n- 取样_取样井段_m\n- 取样_取样时间\n- 油分析_测粘温度_℃\n- 油分析_地面密度_g/cm3\n- 油分析_地面粘度_mPa.s\n- 油分析_凝固点_℃\n- 油分析_含硫_%\n- 油分析_含蜡_%\n- 油分析_H2S 含量_%\n- 结论\n注意：若存在合并行，请将合并内容填充到每一行。",
    "水分析": "请提取'地层水分析'或'水分析'表格。\n目标列(JSON Key)：\n- 序号\n- 层位\n- 取样_取样井号\n- 取样_取样井段_m\n- 取样_取样时间\n- 水分析_Na+_mg/l\n- 水分析_Mg+_mg/l\n- 水分析_Ca+_mg/l\n- 水分析_Cl-_mg/l\n- 水分析_SO4-_mg/l\n- 水分析_CO3-_mg/l\n- 水分析_总矿化度_mg/l\n- 结论\n注意：若存在合并行，请将合并内容填充到每一行。",
    "气分析": "请提取'天然气分析'或'气分析'表格。\n目标列(JSON Key)：\n- 序号\n- 层位\n- 取样_取样井号\n- 取样_取样井段_m\n- 取样_取样时间\n- 气分析_氦\n- 气分析_氢\n- 气分析_氧\n- 气分析_氮\n- 气分析_二氧化碳\n- 气分析_乙烷\n- 气分析_丙烷\n- 气分析_异丁烷\n- 气分析_正丁烷\n- 气分析_新戊烷\n- 气分析_异戊烷\n- 气分析_正戊烷\n- 气分析_己烷\n- 气分析_庚烷和更重组分\n- 气分析_一氧化碳\n- 气分析_硫化氢\n- 气分析_二氧化硫\n注意：若存在合并行，请将合并内容填充到每一行。"
};

const extractJobId = ref(null);
const extractJobInfo = ref(null);
const extracting = ref(false);
const extractConfig = reactive({
  instruction: '',
  outputFmt: 'Excel',
  customFilename: '',
  parseMethod: 'original',
  targetKbId: undefined,
  kbMode: 'existing',
  templateKey: undefined
});
const extractFileList = ref([]);
const extractResults = ref([]);
const extractResultColumns = ref([]);
let extractPoller = null;

const extractStep = computed(() => {
   if (!extractJobInfo.value) return 0;
   const p = extractJobInfo.value.progress || 0;
   if (p < 10) return 0;
   if (p < 50) return 1;
   if (p < 100) return 2;
   return 3; 
});

const onTemplateChange = (val) => {
   if (PRESET_TEMPLATES[val]) {
       extractConfig.instruction = PRESET_TEMPLATES[val];
   }
};

const handleCreateKbAndSelect = async () => {
    if (!createForm.kbName) return message.warning('请输入名称');
    creatingKb.value = true;
    try {
        await api.createKb(createForm);
        message.success('创建成功');
        await loadKbList();
        
        const newKb = kbList.value.find(k => k.kbName === createForm.kbName);
        if (newKb) {
            extractConfig.targetKbId = newKb.kbId;
            extractConfig.kbMode = 'existing';
        }
    } catch(e) { message.error('创建失败: ' + e.message); } 
    finally { creatingKb.value = false; }
};

const startExtractionJob = async () => {
    if (!extractFileList.value.length) return message.warning('请上传文件');
    if (!extractConfig.instruction) return message.warning('请输入提取指令');
    
    if (extractConfig.kbMode === 'new' && !extractConfig.targetKbId) {
        return message.warning('请先点击"立即创建"按钮创建知识库');
    }
    if (!extractConfig.targetKbId) return message.warning('请选择目标知识库');

    extracting.value = true;
    try {
        const outputFilename = extractConfig.customFilename?.trim();
        if (outputFilename) {
            const checkRes = await api.checkExtractionFilename({
                kbId: extractConfig.targetKbId,
                filename: outputFilename,
            });
            if (checkRes.exists || (checkRes.conflicts && checkRes.conflicts.length)) {
                message.error('目标知识库中已存在同名提取结果，请修改文件名');
                return;
            }
        }

        const fd = new FormData();
        fd.append('file', extractFileList.value[0].originFileObj);
        fd.append('instruction', extractConfig.instruction);
        fd.append('kb_id', extractConfig.targetKbId);
        fd.append('output_format', extractConfig.outputFmt);
        if(outputFilename) fd.append('custom_filename', outputFilename);
        fd.append('parse_method', extractConfig.parseMethod);

        const res = await api.startExtraction(fd);
        extractJobId.value = res.jobId;
        extractJobInfo.value = { status: 'starting', progress: 0 };
        message.success('任务已启动');
        startPolling();
    } catch(e) {
        message.error('启动失败: ' + (e.message || e));
    } finally {
        extracting.value = false;
    }
};

const startPolling = () => {
   if (extractPoller) clearInterval(extractPoller);
   extractPoller = setInterval(async () => {
      if (!extractJobId.value) return;
      try {
         const res = await api.getExtractionStatus({ jobId: extractJobId.value });
         extractJobInfo.value = res;
         if (res.status === 'completed' || res.status === 'failed') {
             clearInterval(extractPoller);
             if (res.status === 'completed') {
                 message.success('提取完成');
                 extractResults.value = res.data || [];
                 if (extractResults.value.length > 0) {
                     extractResultColumns.value = Object.keys(extractResults.value[0]).map(k => ({ title: k, dataIndex: k }));
                 }
             } else {
                 message.error('任务失败: ' + res.error);
             }
         }
      } catch(e) {
         console.error(e);
      }
   }, 2000);
};

const resetExtraction = () => {
    extractJobId.value = null;
    extractJobInfo.value = null;
    extractResults.value = [];
    if(extractPoller) clearInterval(extractPoller);
};

const saveExtractionChanges = async () => {
    try {
        const dataToSave = JSON.parse(JSON.stringify(extractResults.value));
        await api.updateExtractionResult({
            jobId: extractJobId.value,
            data: dataToSave
        });
        message.success('保存成功, 已更新入库');
    } catch(e) {
        message.error('保存失败');
    }
};

const getExtractPageUrl = (page) => {
    return api.getExtractionImageUrl({ jobId: extractJobId.value, page });
};

const getExtractionContentUrl = () => {
    return api.buildMultimodalAssetUrl('/extraction/content', { jobId: extractJobId.value });
};

// 监听
watch(activeTab, (val) => {
  if (val === 'manage' && !kbList.value.length) loadKbList();
  if (val === 'index' && !kbList.value.length) loadKbList();
  if (val === 'structured' && !structuredSupportedTypes.value.length) loadStructuredDashboard();
  if (val === 'preprocess' && !preprocessMethods.value.length) loadPreprocessMethods();
});

onMounted(() => {
  loadKbList();
});
</script>

<style scoped>
.multimodal-container {
  padding: 24px;
  background: #f0f2f5;
  min-height: 100vh;
}
.main-tabs {
  background: #fff;
  padding: 16px;
  border-radius: 8px;
}

/* KB Manage Layout */
.kb-manage-layout {
  display: flex;
  height: 700px;
  border: 1px solid #e8e8e8;
}
.kb-sidebar {
  width: 250px;
  border-right: 1px solid #e8e8e8;
  display: flex;
  flex-direction: column;
}
.sidebar-header {
  padding: 12px;
  border-bottom: 1px solid #e8e8e8;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: bold;
}
.kb-list-wrapper {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
.kb-list {
  /* overflow removed, handled by wrapper */
}
.kb-item {
  padding: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid #f0f0f0;
}
.kb-item:hover, .kb-item.active {
  background: #e6f7ff;
}
.kb-icon { font-size: 20px; }
.kb-info { flex: 1; }
.kb-name { font-weight: 500; font-size: 14px; }
.kb-meta { font-size: 12px; color: #999; }
.del-icon { color: #ff4d4f; display: none; }
.kb-item:hover .del-icon { display: block; }

.kb-content {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
}
.content-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.kb-id-tag {
  font-size: 12px;
  background: #eee;
  padding: 2px 6px;
  border-radius: 4px;
  color: #666;
  margin-left: 10px;
}

/* Image Grid */
.image-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 16px;
}
.image-total {
  color: #666;
  font-size: 13px;
}
.image-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
  cursor: zoom-in;
}
.img-wrapper {
  height: 150px;
  overflow: hidden;
  display: flex;
  justify-content: center;
  align-items: center;
  background: #000;
}
.img-wrapper img {
  max-width: 100%;
  max-height: 100%;
}
.img-meta-info {
  font-size: 12px;
  color: #888;
  margin-bottom: 6px;
}
.desc-input {
  font-size: 12px;
}
.image-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.preview-image-row {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 12px;
  width: 100%;
}
.preview-image-row img {
  width: 96px;
  height: 72px;
  object-fit: contain;
  background: #111;
}
.image-summary {
  margin-top: 6px;
  color: #555;
  line-height: 1.5;
  white-space: pre-wrap;
}

/* Search */
.search-container {
  display: grid;
  gap: 16px;
  width: 100%;
  max-width: 1180px;
  margin: 0 auto;
  padding: 4px 0 12px;
}

.search-panel {
  min-width: 0;
  border: 1px solid #dfe5eb;
  border-radius: 8px;
  padding: 18px;
  background: #fff;
}

.search-condition-panel {
  background: #fafbfc;
}

.search-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.search-panel-header h3 {
  margin: 0;
  color: #1f2937;
  font-size: 16px;
  line-height: 1.4;
}

.search-result-count {
  color: #6b7280;
  font-size: 13px;
}

.search-form-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 14px;
  align-items: end;
}

.search-field {
  display: grid;
  grid-column: span 4;
  gap: 7px;
  min-width: 0;
}

.search-field > span {
  color: #4b5563;
  font-size: 13px;
  font-weight: 600;
}

.search-query-field {
  grid-column: 1 / -1;
}

.search-topk-field {
  grid-column: span 2;
}

.search-topk-field :deep(.ant-input-number) {
  width: 100%;
}

.search-form-actions {
  display: flex;
  grid-column: span 2;
  align-items: center;
  justify-content: flex-end;
}

.search-form-actions .ant-btn {
  width: 100%;
}

.search-result-collapse {
  border: 0;
  background: transparent;
}

.search-result-collapse :deep(.ant-collapse-item) {
  overflow: hidden;
  margin-bottom: 10px;
  border: 1px solid #e1e7ec;
  border-radius: 8px;
  background: #f8fafb;
}

.search-result-collapse :deep(.ant-collapse-item:last-child) {
  margin-bottom: 0;
  border-bottom: 1px solid #e1e7ec;
}

.search-result-collapse :deep(.ant-collapse-header) {
  min-height: 48px;
  padding: 13px 16px !important;
  align-items: center !important;
}

.search-result-collapse :deep(.ant-collapse-content) {
  border-top: 1px solid #e1e7ec;
}

.search-result-collapse :deep(.ant-collapse-content-box) {
  padding: 16px;
  background: #fff;
}

.search-result-summary {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  min-width: 0;
  color: #64748b;
  font-size: 12px;
}

.search-result-summary strong {
  color: #1f2937;
  font-size: 14px;
}

.search-result-summary span {
  overflow-wrap: anywhere;
}

.result-content {
  min-width: 0;
  overflow-x: auto;
  overflow-wrap: anywhere;
  border: 1px solid #e5eaef;
  border-radius: 8px;
  padding: 14px 16px;
  background: #fff;
  color: #273444;
  font-size: 15px;
  line-height: 1.75;
}

.result-content :deep(img) {
  display: block;
  width: auto;
  max-width: 100%;
  max-height: 560px;
  object-fit: contain;
  border-radius: 8px;
  margin: 12px 0;
  border: 1px solid #e5eaef;
  background: #fff;
  cursor: zoom-in;
}

.result-content :deep(p) {
  margin: 0 0 10px;
}

.result-content :deep(p:last-child) {
  margin-bottom: 0;
}

.result-content :deep(table) {
  width: 100%;
  min-width: 520px;
  border-collapse: collapse;
}

.result-content :deep(th),
.result-content :deep(td) {
  border: 1px solid #dce3e8;
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}

.result-content :deep(th) {
  background: #f3f6f8;
  color: #253442;
  font-weight: 600;
}

.search-result-source {
  display: grid;
  gap: 9px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #e1e7ec;
}

.search-result-source > strong {
  color: #374151;
  font-size: 13px;
}

.search-source-rows {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.search-source-rows span {
  display: inline-flex;
  gap: 6px;
  max-width: 100%;
  overflow-wrap: anywhere;
  border: 1px solid #dfe5eb;
  border-radius: 999px;
  padding: 5px 9px;
  background: #f8fafb;
  color: #5f6f7d;
  font-size: 12px;
  line-height: 1.4;
}

.search-source-rows b {
  flex: 0 0 auto;
  color: #273444;
}

.clickable-item {
  cursor: pointer;
}
.clickable-item:hover {
  background: #f0f0f0;
}
.index-container {
  padding: 20px;
}
.index-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}
.index-stats {
  margin-bottom: 16px;
  padding: 16px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
}
.chunk-preview {
  max-height: 96px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
}
.pager-bar {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  color: #666;
}
.structured-container {
  padding: 20px;
}
.panel-block {
  margin-bottom: 16px;
  padding: 16px;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  background: #fff;
}
.panel-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  font-weight: 600;
}
.json-panel {
  max-height: 260px;
  overflow: auto;
  padding: 12px;
  margin-bottom: 12px;
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
}
.structured-query-line {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin: 12px 0;
}
.preprocess-container {
  padding: 20px;
}
.method-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 8px;
  max-height: 220px;
  overflow: auto;
  padding: 8px;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
}
.presets {
  margin-top: 8px;
  display: flex;
  gap: 8px;
}
.presets .ant-tag { cursor: pointer; }

@media (max-width: 900px) {
  .multimodal-container {
    padding: 12px;
  }

  .main-tabs {
    padding: 10px;
  }

  .search-field,
  .search-topk-field,
  .search-form-actions {
    grid-column: 1 / -1;
  }

  .search-form-actions .ant-btn {
    width: auto;
    min-width: 120px;
  }

  .search-result-summary {
    display: grid;
    gap: 4px;
  }
}
</style>
