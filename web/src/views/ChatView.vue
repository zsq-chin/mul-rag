<template> 
  <div class="chat-container">
    <div class="conversations" :class="{ 'is-open': state.isSidebarOpen }">
      <div class="actions">
        <!-- <div class="action new" @click="addNewConv"><FormOutlined /></div> -->
         <span class="header-title"></span>
        <div class="action close" @click="state.isSidebarOpen = false">
          <PanelLeftClose size="20" color="var(--gray-800)"/>
        </div>
      </div>
      <div class="conversation-list" v-if="state.hasLoadedHistory">
        <div class="conversation"
          v-for="(state, index) in convs"
          :key="index"
          :class="{ active: curConvId === index }"
          @click="goToConversation(index)">
          <div class="conversation__title">{{ state.title }}</div>
          <div class="conversation__actions">
            <div class="conversation__delete" @click.stop="renameConv(index)"><EditOutlined /></div>
            <div class="conversation__delete" @click.stop="delConv(index)"><DeleteOutlined /></div>
          </div>
        </div>
      </div>
    </div>
    <a-modal :open="renameModal.open" title="修改对话标题" @ok="handleRename" @cancel="renameModal.open=false" style="margin-top: 50px;">
      <a-input v-model:value="renameModal.title" placeholder="请输入新的对话标题" style="margin-top: 20px;margin-bottom: 20px"/>
      <template #footer>
        <a-button key="back" @click="renameModal.open=false">取消</a-button>
        <a-button key="submit" type="primary" @click="handleRename">确定</a-button>
      </template>
    </a-modal>
    <!-- @rename-title , @newconv 接受子组件传递的事件信息，触发renameTitle与addNewConv函数的调用-->
    <!-- 只要convs[curConvId]发生变化，都会重新传递给ChatComponent -->
    <ChatComponent
      :conv="convs[curConvId]"
      :state="state"
      @rename-title="renameTitle"
      @newconv="addNewConv"
      @sent-message="saveRecordChanges()"
    />
  </div>
</template>

<script setup>
import { reactive, ref, watch, onMounted, computed } from 'vue'
import { DeleteOutlined , EditOutlined } from '@ant-design/icons-vue'
import ChatComponent from '@/components/ChatComponent.vue'
import { MessageSquareMore, PanelLeftClose } from 'lucide-vue-next'
import { chatRecordApi } from '@/apis/auth_api'
import { useUserStore } from '@/stores/user'
import { message } from 'ant-design-vue'

const generateUniqueHash = () => {
  const timestamp = Date.now().toString(36); 
  const randomStr = Math.random().toString(36).slice(2, 8);
  return timestamp + randomStr;
}

const convs = reactive([{
  id: generateUniqueHash(),
  title: '新对话',
  history: [],
  messages: [],
  inputText: ''
}]) 

const state = reactive({
  isSidebarOpen: JSON.parse(localStorage.getItem('chat-sidebar-open') || 'true'),
  hasLoadedHistory: false
})

const renameModal = reactive({
  open: false,
  index: 0,
  title: ''
})

// Watch isSidebarOpen and save to localStorage
watch(
  () => state.isSidebarOpen,
  (newValue) => {
    localStorage.setItem('chat-sidebar-open', JSON.stringify(newValue))
  }
)

// 当前对话的顺序的id（0.1.2.3）并非对话id（hash值）
const curConvId = ref(0)
const generateRandomHash = (length) => {
    let chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    let hash = '';
    for (let i = 0; i < length; i++) {
        hash += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return hash;
}

const renameTitle = (newTitle) => {
  convs[curConvId.value].title = newTitle
  saveRecordChanges()
}

const goToConversation = (index) => {
  curConvId.value = index
  console.log("curConvId.value",convs[curConvId.value])
}

const addNewConv = () => {
  curConvId.value = 0
  if (convs.length > 0 && convs[0].messages.length === 0) {
    return
  }
  // 最前面插入一个新的对话对象
  convs.unshift({
    id: generateUniqueHash(),
    title: `新对话`,
    history: [],
    messages: [],
    inputText: ''
  })
}

const delConv = async (index) =>  {
  try {
    await chatRecordApi.deleteChatRecord(convs[index].id)
  } catch (e) {
    message.error('保存聊天记录失败')
  }
  convs.splice(index, 1)
  if (index < curConvId.value) {
    curConvId.value -= 1
  } else if (index === curConvId.value) {
    curConvId.value = 0
  }

  if (convs.length === 0) {
    addNewConv()
  }
}

const renameConv = (index) => {
  renameModal.open = true
  renameModal.index = index
  renameModal.title = convs[index].title
}

const handleRename = () => {
  if (renameModal.title && renameModal.title.trim() !== '') {
    convs[renameModal.index].title = renameModal.title
    renameModal.open = false
    saveChatRecords(convs[renameModal.index])
  }
}

const saveRecordChanges = () => {
  saveChatRecords(convs[curConvId.value])
}

// 加载聊天记录
const loadChatRecords = async () => {
  try {
    const res = await chatRecordApi.getChatRecords()
    if (res.length !== 0) {
      convs.splice(0, convs.length, ...res.map(r => r.content))
    }
    state.hasLoadedHistory = true
  } catch (e) {
    message.error('加载聊天记录失败')
  }
}

// 保存聊天记录
const saveChatRecords = async (changedConv) => {
  try {
    await chatRecordApi.saveChatRecords(changedConv)
  } catch (e) {
    message.error('保存聊天记录失败')
  }
}


// onMounted时从后端加载
onMounted(() => {
  loadChatRecords()
})
</script>

<style lang="less" scoped>
@import '@/assets/main.css';

.chat-container {
  display: flex;
  width: 100%;
  height: 100%;
  position: relative;
}

.conversations {
  max-width: 380px;
  border-right: 1px solid var(--main-light-3);
  background-color: var(--gray-100);
  transition: all 0.3s ease;
  white-space: nowrap; /* 防止文本换行 */
  overflow: hidden; /* 确保内容不溢出 */

  &.is-open {
    width: 18%;
  }

  &:not(.is-open) {
    width: 0;
    padding: 0;
    overflow: hidden;
  }

  & .actions {
    height: var(--header-height);
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px;
    z-index: 9;
    border-bottom: 1px solid var(--main-light-3);

    .header-title {
      font-weight: bold;
      user-select: none;
      white-space: nowrap;
      overflow: hidden;
    }

    .action {
      font-size: 1.2rem;
      width: 2.5rem;
      height: 2.5rem;
      display: flex;
      justify-content: center;
      align-items: center;
      border-radius: 8px;
      color: var(--gray-800);
      cursor: pointer;

      &:hover {
        background-color: var(--main-light-3);
      }

      .nav-btn-icon {
        width: 1.2rem;
        height: 1.2rem;
      }
    }
  }

  .conversation-list {
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    max-height: 100%;
  }

  .conversation-list .conversation {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px;
    cursor: pointer;
    width: 100%;
    user-select: none;
    transition: background-color 0.2s ease-in-out;

    &__title {
      color: var(--gray-700);
      white-space: nowrap; /* 禁止换行 */
      overflow: hidden;    /* 超出部分隐藏 */
      text-overflow: ellipsis; /* 显示省略号 */
    }

    &__actions {
      display: flex;
      align-items: center;
      gap: 14px;
    }

    &__delete {
      display: none;
      color: var(--gray-500);
      transition: all 0.2s ease-in-out;

      &:hover {
        color: #F93A37;
        background-color: #EEE;
      }
    }

    &.active {
      border-right: 3px solid var(--main-500);
      padding-right: 13px;
      background-color: var(--gray-200);

      & .conversation__title {
        color: var(--gray-1000);
      }
    }

    &:not(.active):hover {
      background-color: var(--main-light-3);

      & .conversation__delete {
        display: block;
      }
    }
  }
}

.conversation-list::-webkit-scrollbar {
  position: absolute;
  width: 4px;
}

.conversation-list::-webkit-scrollbar-track {
  background: transparent;
  border-radius: 4px;
}

.conversation-list::-webkit-scrollbar-thumb {
  background: var(--gray-400);
  border-radius: 4px;
}

.conversation-list::-webkit-scrollbar-thumb:hover {
  background: rgb(100, 100, 100);
  border-radius: 4px;
}

.conversation-list::-webkit-scrollbar-thumb:active {
  background: rgb(68, 68, 68);
  border-radius: 4px;
}

@media (max-width: 520px) {
  .conversations {
    position: absolute;
    z-index: 101;
    width: 300px;
    height: 100%;
    border-radius: 0 16px 16px 0;
    box-shadow: 0 0 10px 1px rgba(0, 0, 0, 0.05);

    &:not(.is-open) {
      width: 0;
      padding: 0;
      overflow: hidden;
    }
  }
}
</style>
