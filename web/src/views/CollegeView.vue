<template>
  <div class="college-container">
    <div class="query-section">
      <h2>K80数据库查询</h2>
      
      <!-- 用户查询表单 -->
      <div class="query-form">
        <h3>用户查询</h3>
        <a-form layout="inline">
          <a-form-item label="用户名">
            <a-input v-model:value="userQuery.username" placeholder="输入用户名" />
          </a-form-item>
          <a-form-item label="角色">
            <a-input v-model:value="userQuery.role" placeholder="输入角色" />
          </a-form-item>
          <a-form-item>
            <a-button type="primary" @click="searchUsers" :loading="userLoading">查询</a-button>
          </a-form-item>
        </a-form>
        
        <a-table 
          :columns="userColumns" 
          :dataSource="userData" 
          :loading="userLoading"
          :pagination="userPagination"
          @change="handleUserTableChange"
          rowKey="id"
          bordered
          size="small"
        />
      </div>

      <!-- 指南记录查询表单 -->
      <div class="query-form">
        <h3>指南记录查询</h3>
        <a-form layout="inline">
          <a-form-item label="指南ID">
            <a-input v-model:value="guideQuery.guide_id" placeholder="输入指南ID" />
          </a-form-item>
          <a-form-item label="用户ID">
            <a-input-number v-model:value="guideQuery.user_id" placeholder="输入用户ID" />
          </a-form-item>
          <a-form-item>
            <a-button type="primary" @click="searchGuideRecords" :loading="guideLoading">查询</a-button>
          </a-form-item>
        </a-form>
        
        <a-table 
          :columns="guideColumns" 
          :dataSource="guideData" 
          :loading="guideLoading"
          :pagination="guidePagination"
          @change="handleGuideTableChange"
          rowKey="id"
          bordered
          size="small"
        />
      </div>

      <!-- 用户指南记录查询 -->
      <div class="query-form">
        <h3>用户指南记录查询</h3>
        <a-form layout="inline">
          <a-form-item label="用户ID">
            <a-input-number v-model:value="userGuideQuery.user_id" placeholder="输入用户ID" />
          </a-form-item>
          <a-form-item>
            <a-button type="primary" @click="searchUserGuideRecords" :loading="userGuideLoading">查询</a-button>
          </a-form-item>
        </a-form>
        
        <a-table 
          :columns="userGuideColumns" 
          :dataSource="userGuideData" 
          :loading="userGuideLoading"
          :pagination="userGuidePagination"
          @change="handleUserGuideTableChange"
          rowKey="id"
          bordered
          size="small"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { k80Api } from '@/apis/k80'
import { message } from 'ant-design-vue'

// 用户查询相关
const userQuery = reactive({
  username: 'liuxu',
  role: 'admin'
})
const userData = ref([])
const userLoading = ref(false)
const userPagination = reactive({
  current: 1,
  pageSize: 10,
  total: 0
})
const userColumns = [
  {
    title: 'ID',
    dataIndex: 'id',
    key: 'id'
  },
  {
    title: '用户名',
    dataIndex: 'username',
    key: 'username'
  },
  {
    title: '角色',
    dataIndex: 'role',
    key: 'role'
  },
  {
    title: '创建时间',
    dataIndex: 'created_at',
    key: 'created_at'
  },
  {
    title: '最后登录',
    dataIndex: 'last_login',
    key: 'last_login'
  }
]

// 指南记录查询相关
const guideQuery = reactive({
  guide_id: '1',
  user_id: 1231
})
const guideData = ref([])
const guideLoading = ref(false)
const guidePagination = reactive({
  current: 1,
  pageSize: 10,
  total: 0
})
const guideColumns = [
  {
    title: 'ID',
    dataIndex: 'id',
    key: 'id'
  },
  {
    title: '指南ID',
    dataIndex: 'guide_id',
    key: 'guide_id'
  },
  {
    title: '内容',
    dataIndex: 'content',
    key: 'content',
    ellipsis: true
  },
  {
    title: '用户ID',
    dataIndex: 'user_id',
    key: 'user_id'
  },
  {
    title: '更新时间',
    dataIndex: 'updatetime',
    key: 'updatetime'
  }
]

// 用户指南记录查询相关
const userGuideQuery = reactive({
  user_id: 1231
})
const userGuideData = ref([])
const userGuideLoading = ref(false)
const userGuidePagination = reactive({
  current: 1,
  pageSize: 10,
  total: 0
})
const userGuideColumns = [
  {
    title: 'ID',
    dataIndex: 'id',
    key: 'id'
  },
  {
    title: '指南ID',
    dataIndex: 'guide_id',
    key: 'guide_id'
  },
  {
    title: '内容',
    dataIndex: 'content',
    key: 'content',
    ellipsis: true
  },
  {
    title: '更新时间',
    dataIndex: 'updatetime',
    key: 'updatetime'
  }
]

// 用户查询方法
const searchUsers = async () => {
  try {
    userLoading.value = true
    const res = await k80Api.getUsers(userQuery)
    userData.value = res
    userPagination.total = res.length
  } catch (e) {
    message.error('查询用户失败')
  } finally {
    userLoading.value = false
  }
}

// 指南记录查询方法
const searchGuideRecords = async () => {
  try {
    guideLoading.value = true
    const res = await k80Api.getGuideRecords(guideQuery)
    guideData.value = res
    guidePagination.total = res.length
  } catch (e) {
    message.error('查询指南记录失败')
  } finally {
    guideLoading.value = false
  }
}

// 用户指南记录查询方法
const searchUserGuideRecords = async () => {
  try {
    userGuideLoading.value = true
    const res = await k80Api.getUserGuideRecords(userGuideQuery.user_id)
    userGuideData.value = res
    userGuidePagination.total = res.length
  } catch (e) {
    message.error('查询用户指南记录失败')
  } finally {
    userGuideLoading.value = false
  }
}

// 表格分页变化处理
const handleUserTableChange = (pagination) => {
  userPagination.current = pagination.current
  userPagination.pageSize = pagination.pageSize
}

const handleGuideTableChange = (pagination) => {
  guidePagination.current = pagination.current
  guidePagination.pageSize = pagination.pageSize
}

const handleUserGuideTableChange = (pagination) => {
  userGuidePagination.current = pagination.current
  userGuidePagination.pageSize = pagination.pageSize
}
</script>

<style scoped>
.college-container {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}

.query-section {
  margin-bottom: 30px;
}

.query-form {
  margin-bottom: 30px;
  padding: 20px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

h2 {
  margin-bottom: 20px;
  color: #333;
}

h3 {
  margin-bottom: 15px;
  color: #555;
}

.ant-form-inline .ant-form-item {
  margin-right: 16px;
  margin-bottom: 16px;
}

.ant-table {
  margin-top: 16px;
}
</style>
