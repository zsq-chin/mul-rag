<script setup>
import { ref, reactive, computed, KeepAlive, onMounted, onBeforeUnmount } from 'vue'
import { MenuFoldOutlined, MenuUnfoldOutlined, TeamOutlined } from '@ant-design/icons-vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import {
  BugOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons-vue'
import { Bot, Flame ,Waypoints,TextSearch ,Speech ,Share2 ,Captions ,Milestone, LibraryBig,BookOpenCheck , MessageSquareMore, Settings,Hourglass,PencilLine ,Move, BarChart3, Layers, Moon, Sun } from 'lucide-vue-next';

import { useConfigStore } from '@/stores/config'
import { useDatabaseStore } from '@/stores/database'
import { useUserStore } from '@/stores/user'
import { useThemeStore } from '@/stores/theme'
import { navigationForRole } from '@/utils/access.mjs'
import DebugComponent from '@/components/DebugComponent.vue'
import UserInfoComponent from '@/components/UserInfoComponent.vue'
import UserManagementComponent from '@/components/UserManagementComponent.vue'

const configStore = useConfigStore()
const databaseStore = useDatabaseStore()
const userStore = useUserStore()
const themeStore = useThemeStore()

const layoutSettings = reactive({
  showDebug: false,
  useTopBar: true, // 是否使用顶栏
  navCollapsed: false, // 导航栏折叠状态
})

const getRemoteConfig = () => {
  configStore.refreshConfig()
}

const getRemoteDatabase = () => {
  if (!configStore.config.enable_knowledge_base) {
    return
  }
  databaseStore.refreshDatabase()
}


// 打印当前页面的路由信息，使用 vue3 的 setup composition API
const route = useRoute()

// 用户管理抽屉
const userDrawerOpen = ref(false)
const windowWidth = ref(typeof window === 'undefined' ? 1024 : window.innerWidth)
const drawerWidth = computed(() => windowWidth.value < 768 ? '100%' : 760)

const updateWindowWidth = () => {
  windowWidth.value = window.innerWidth
}

onMounted(() => {
  if (userStore.isSuperAdmin) {
    getRemoteConfig()
    getRemoteDatabase()
  }
  window.addEventListener('resize', updateWindowWidth)
})

onBeforeUnmount(() => window.removeEventListener('resize', updateWindowWidth))

// 图标映射
const ICON_MAP = {
  chat: MessageSquareMore,
  users: TeamOutlined,
  graph: Share2,
  database: BookOpenCheck,
  statistics: BarChart3,
  multimodal: Layers,
}

// 基于角色的导航列表
const mainList = computed(() => {
  const items = navigationForRole(userStore.userRole)
  return items.map(item => ({
    ...item,
    icon: ICON_MAP[item.key] || MessageSquareMore,
    activeIcon: ICON_MAP[item.key] || MessageSquareMore,
  }))
})
</script>

<template>
  <div class="app-layout" :class="{ 'use-top-bar': layoutSettings.useTopBar }">

    <div class="collapse-button">
      <a-button
        @click="layoutSettings.navCollapsed = !layoutSettings.navCollapsed"
        :title="'折叠导航栏'"
        type="text"
        shape="circle"
      >
        <template #icon>
          <MenuFoldOutlined v-if="!layoutSettings.navCollapsed" />
          <MenuUnfoldOutlined v-else />
        </template>
      </a-button>
    </div>

    <!-- <div class="debug-button">
      <a-float-button
        @click="layoutSettings.showDebug = !layoutSettings.showDebug"
        tooltip="调试面板"
      >
        <template #icon>
          <BugOutlined />
        </template>
      </a-float-button>
      <a-drawer
        v-model:open="layoutSettings.showDebug"
        title="调试面板"
        width="800"
        :contentWrapperStyle="{ maxWidth: '100%'}"
        placement="right"
      >
        <DebugComponent />
      </a-drawer>
    </div> -->
    <div class="main-content" :class="{ 'header-collapsed': layoutSettings.navCollapsed, 'top-bar': layoutSettings.useTopBar }">
      <Transition name="fade-header">
        <div
          class="header"
          :class="{ 'top-bar': layoutSettings.useTopBar }"
          v-if="!layoutSettings.navCollapsed"
        >
          <div class="logo circle">
            <router-link to="/">
              
              <span class="logo-text">压裂大模型智能问答</span>
            </router-link>
          </div>
          <div class="nav">
            <!-- 使用mainList渲染导航项 -->
            <template v-for="item in mainList" :key="item.key">
              <!-- 有路径的导航项 -->
              <RouterLink
                v-if="item.path"
                :to="item.path"
                class="nav-item"
                active-class="active">
                <component class="icon" :is="route.path.startsWith(item.path) ? item.activeIcon : item.icon" size="22"/>
                <span class="text">{{item.name}}</span>
              </RouterLink>
              <!-- 动作项（如用户管理抽屉） -->
              <button
                v-else
                type="button"
                class="nav-item"
                @click="item.action === 'users' ? (userDrawerOpen = true) : null"
                :aria-label="item.name"
              >
                <component class="icon" :is="item.icon" size="22"/>
                <span class="text">{{item.name}}</span>
              </button>
            </template>

            <a-tooltip placement="right">
              <template #title>后端疑似没有正常启动或者正在繁忙中，请刷新一下或者检查 docker logs api-dev</template>
              <div class="nav-item warning" v-if="userStore.isSuperAdmin && !configStore.config._config_items">
                <component class="icon" :is="ExclamationCircleOutlined" />
                <span class="text">警告</span>
              </div>
            </a-tooltip>
          </div>
          <div class="fill" style="flex-grow: 1;"></div>

          <a-tooltip placement="right">
            <template #title>{{ themeStore.mode === 'dark' ? '切换到浅色模式' : '切换到深色模式' }}</template>
            <button
              type="button"
              class="nav-item theme-toggle"
              :aria-label="themeStore.mode === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
              @click="themeStore.toggle"
            >
              <Sun v-if="themeStore.mode === 'dark'" class="icon" :size="20" />
              <Moon v-else class="icon" :size="20" />
            </button>
          </a-tooltip>

          <!-- <div class="nav-item api-docs">
            <a-tooltip placement="right">
              <template #title>接口文档 {{ apiDocsUrl }}</template>
              <a :href="apiDocsUrl" target="_blank" class="github-link">
                <ApiOutlined class="icon" style="color: #222;"/>
              </a>
            </a-tooltip>
          </div> -->

          <!-- 用户信息组件 -->
          <div class="nav-item user-info">
            <a-tooltip placement="right">
              <template #title>用户信息</template>
              <UserInfoComponent />
            </a-tooltip>
          </div>

          <!-- <RouterLink class="nav-item setting" to="/setting" active-class="active">
            <a-tooltip placement="right">
              <template #title>设置</template>
              <Settings />
            </a-tooltip>
          </RouterLink> -->
        </div>
      </Transition>
      <div class="header-mobile">
        <template v-for="item in mainList" :key="`mobile-${item.key}`">
          <RouterLink
            v-if="item.path"
            :to="item.path"
            class="nav-item"
            active-class="active"
          >
            <component class="icon" :is="item.icon" size="20" />
            <span class="text">{{ item.name }}</span>
          </RouterLink>
          <button
            v-else
            type="button"
            class="nav-item"
            :aria-label="item.name"
            @click="item.action === 'users' ? (userDrawerOpen = true) : null"
          >
            <component class="icon" :is="item.icon" size="20" />
            <span class="text">{{ item.name }}</span>
          </button>
        </template>
        <button
          type="button"
          class="nav-item mobile-theme-toggle"
          :aria-label="themeStore.mode === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
          @click="themeStore.toggle"
        >
          <Sun v-if="themeStore.mode === 'dark'" class="icon" :size="20" />
          <Moon v-else class="icon" :size="20" />
          <span class="text">主题</span>
        </button>
        <UserInfoComponent class="mobile-user-info" />
      </div>
      <div
        id="app-router-view"
        :class="{ 'with-header': !layoutSettings.navCollapsed, 'with-top-bar': layoutSettings.useTopBar }"
      >
        <router-view v-slot="{ Component, route }">
          <keep-alive v-if="route.meta.keepAlive !== false">
            <component :is="Component" />
          </keep-alive>
          <component :is="Component" v-else />
        </router-view>
      </div>

      <!-- 用户管理抽屉 -->
      <a-drawer
        v-if="userStore.isAdmin"
        v-model:open="userDrawerOpen"
        title="用户管理"
        placement="right"
        :width="drawerWidth"
        :destroy-on-close="false"
      >
        <UserManagementComponent :actor-role="userStore.userRole" />
      </a-drawer>
    </div>
  </div>
</template>

<style lang="less" scoped>
@import '@/assets/main.css';

:root {
  --header-width: 60px;
}

.app-layout {
  display: flex;
  flex-direction: row;
  width: 100%;
  height: 100vh;
  min-width: var(--min-width);

  .header-mobile {
    display: none;
  }

.debug-button {
  position: fixed;
  z-index: 100;
  right: 2vh;
  bottom: 8vh;
  cursor: pointer;
}
.collapse-button {
  position: fixed;
  z-index: 100;
  right: 14px;
  top: 9px;
  cursor: pointer;
  width: 42px;
  height: 42px;
  background-color: var(--surface-raised);
  border-radius: 50%;
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.15);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.3s ease;
  opacity: 0.7;
}

.collapse-button:hover {
  background-color: var(--hover);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.25);
}

.collapse-button .ant-btn {
  border: none;
  background: transparent;
  padding: 0;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}


.header {
  transition: opacity 0.3s ease;
}
}

div.header, #app-router-view {
  height: 100%;
  max-width: 100%;
  user-select: none;
}

#app-router-view {
  flex: 1 1 auto;
  // overflow-y: hidden;
}

.header {
  display: flex;
  flex-direction: column;
  flex: 0 0 var(--header-width);
  justify-content: flex-start;
  align-items: center;
  background-color: var(--gray-100);
  height: 100%;
  width: var(--header-width);
  border-right: 1px solid var(--gray-300);

  .logo {
    width: 40px;
    height: 40px;
    margin: 14px 0 14px 0;

    img {
      width: 100%;
      height: 100%;
      border-radius: 4px;  // 50% for circle
    }

    .logo-text {
      display: none;
    }

    & > a {
      text-decoration: none;
      font-size: 24px;
      font-weight: bold;
      color: var(--text-primary);
    }
  }

  .nav-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    width: 52px;
    padding: 4px;
    padding-top: 10px;
    border: 1px solid transparent;
    border-radius: 8px;
    background-color: transparent;
    color: var(--text-primary);
    font-size: 20px;
    transition: background-color 0.2s ease-in-out;
    margin: 0;
    text-decoration: none;
    cursor: pointer;
    font-family: inherit;

    &.github {
      padding: 10px 12px;
      &:hover {
        background-color: transparent;
        border: 1px solid transparent;
      }

      .github-link {
        display: flex;
        flex-direction: column;
        align-items: center;
        color: inherit;
      }

      .github-stars {
        display: flex;
        align-items: center;
        font-size: 12px;
        margin-top: 4px;

        .star-icon {
          color: #f0a742;
          font-size: 12px;
          margin-right: 2px;
        }

        .star-count {
          font-weight: 600;
        }
      }
    }

    &.api-docs {
      padding: 10px 12px;
    }
    &.active {
      font-weight: bold;
      color: var(--main-600);
      background-color: var(--surface);
      border: 1px solid var(--surface);
    }

    &.warning {
      color: red;
    }

    &:hover {
      background-color: var(--hover);
      backdrop-filter: blur(10px);
    }

    .text {
      font-size: 12px;
      margin-top: 4px;
      text-align: center;
    }
  }

  .setting {
    width: auto;
    font-size: 20px;
    color: var(--text-primary);
    margin-bottom: 8px;
    padding: 16px 12px;

    &:hover {
      cursor: pointer;
    }
  }
}

.header .nav {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  align-items: center;
  position: relative;
  height: 45px;
  gap: 16px;
  margin-left: 100px;
}

@media (max-width: 520px) {
  .app-layout {
    flex-direction: column-reverse;
    min-width: 0;
    overflow-x: hidden;

    div.header {
      display: none;
    }

    .debug-panel {
      bottom: 10rem;
    }

  }
  .app-layout div.header-mobile {
    display: flex;
    flex-direction: row;
    width: 100%;
    padding: 0 20px;
    justify-content: flex-start;
    align-items: center;
    gap: 20px;
    overflow-x: auto;
    flex: 0 0 60px;
    border-right: none;
    height: 40px;

    .nav-item {
      display: inline-flex;
      flex: 0 0 auto;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-decoration: none;
      min-width: 48px;
      padding: 4px;
      border: 0;
      background: transparent;
      color: var(--gray-900);
      font: inherit;
      transition: color 0.1s ease-in-out, font-size 0.1s ease-in-out;

      &.active {
        color: var(--text-primary);
        font-size: 1.1rem;
      }

      .text {
        margin-top: 2px;
        font-size: 11px;
        white-space: nowrap;
      }
    }

    .mobile-user-info {
      flex: 0 0 48px;
      min-width: 48px;
    }
  }
  .app-layout .chat-box::webkit-scrollbar {
    width: 0;
  }
}

.app-layout.use-top-bar {
  flex-direction: column;
}

.header.top-bar {
  flex-direction: row;
  flex: 0 0 50px;
  width: 100%;
  height: 8vh;
  border-right: none;
  border-bottom: 1px solid var(--main-light-2);
  background-color: var(--main-light-3);
  padding: 0 72px 0 20px;
  gap: 0px;

  .logo {
    width: fit-content;
    height: 40px;
    margin-right: 16px;
    display: flex;
    align-items: center;

    a {
      display: flex;
      align-items: center;
      text-decoration: none;
      color: inherit;
    }

    img {
      width: 60px;
      height: 60px;
      margin-right: 8px;
    }

    .logo-text {
      display: block;
      font-size: 22px;
      font-weight: 600;
      font-family: cursive;
      letter-spacing: 0.5px;
      color: var(--main-600);
      white-space: nowrap;
    }
  }

  .nav {
    flex-direction: row;
    height: auto;
    // gap: 10px;
  }

  .nav-item {
    flex-direction: row;
    width: auto;
    padding: 4px 16px;
    margin: 0;

    .icon {
      margin-right: 8px;
      font-size: 15px; // 减小图标大小
      border: none;
      outline: none;

      &:focus, &:active {
        border: none;
        outline: none;
      }
    }

    .text {
      margin-top: 0;
      font-size: 15px;
    }

    &.github, &.setting {
      padding: 8px 12px;

      .icon {
        margin-right: 0;
        font-size: 18px;
      }

      &.active {
        color: var(--main-600);
      }
    }

    &.theme-toggle {
      width: 42px;
      height: 42px;
      padding: 0;

      .icon {
        margin-right: 0;
      }
    }

  }
}

.main-content {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: row;
  transition: margin 0.6s cubic-bezier(0.4, 0, 0.2, 1);

  &.header-collapsed {
    margin-left: 0;
    margin-top: 0;
  }
  &:not(.header-collapsed) {
    // 侧边栏模式
    margin-left: var(--header-width);
    margin-top: 0;
  }
  &.top-bar {
    flex-direction: column;
    transition: margin 0.6s cubic-bezier(0.4, 0, 0.2, 1), padding 0.6s cubic-bezier(0.4, 0, 0.2, 1);
    // 顶栏且未折叠时
    // &:not(.header-collapsed) {
    //   margin-left: 0;
    //   // margin-top: 100px;
    // }
    // // 顶栏且折叠时
    // &.header-collapsed {
    //   margin-left: 0;
    //   margin-top: 0;
    // }
  }
}

.header {
  position: absolute;
  left: 0;
  top: 0;
  z-index: 10;
  transition: opacity 0.6s cubic-bezier(0.4, 0, 0.2, 1), transform 0.6s cubic-bezier(0.4, 0, 0.2, 1);
  will-change: opacity, transform;


  display: flex;
  flex-direction: column;
  flex: 0 0 var(--header-width);
  justify-content: flex-start;
  align-items: center;
  background-color: var(--gray-100);
  // height: 100%;
  width: var(--header-width);
  border-right: 1px solid var(--gray-300);

  .logo {
    width: 40px;
    height: 40px;
    margin: 14px 0 14px 0;

    img {
      width: 100%;
      height: 100%;
      border-radius: 4px;  // 50% for circle
    }

    .logo-text {
      display: none;
    }

    & > a {
      text-decoration: none;
      font-size: 24px;
      font-weight: bold;
      color: var(--text-primary);
    }
  }

  .nav-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    width: 52px;
    padding: 4px;
    padding-top: 10px;
    border: 1px solid transparent;
    border-radius: 8px;
    background-color: transparent;
    color: var(--text-primary);
    font-size: 20px;
    transition: background-color 0.2s ease-in-out;
    margin: 0;
    text-decoration: none;
    cursor: pointer;

    &.github {
      padding: 10px 12px;
      &:hover {
        background-color: transparent;
        border: 1px solid transparent;
      }

      .github-link {
        display: flex;
        flex-direction: column;
        align-items: center;
        color: inherit;
      }

      .github-stars {
        display: flex;
        align-items: center;
        font-size: 12px;
        margin-top: 4px;

        .star-icon {
          color: #f0a742;
          font-size: 12px;
          margin-right: 2px;
        }

        .star-count {
          font-weight: 600;
        }
      }
    }

    &.api-docs {
      padding: 10px 12px;
    }
    &.active {
      font-weight: bold;
      color: var(--main-600);
      background-color: var(--surface);
      border: 1px solid var(--surface);
    }

    &.warning {
      color: red;
    }

    &:hover {
      background-color: var(--hover);
      backdrop-filter: blur(10px);
    }

    .text {
      font-size: 12px;
      margin-top: 4px;
      text-align: center;
    }
  }

  .setting {
    width: auto;
    font-size: 20px;
    color: var(--text-primary);
    margin-bottom: 8px;
    padding: 16px 12px;

    &:hover {
      cursor: pointer;
    }
  }
}


#app-router-view {
  flex: 1 1 auto;
  overflow-y: auto;
  z-index: 1;
  transition: margin 0.6s cubic-bezier(0.4, 0, 0.2, 1), padding 0.6s cubic-bezier(0.4, 0, 0.2, 1);
  margin-left: 0;
  margin-top: 0;
  // 只在 header 显示时设置 margin
  &.with-header:not(.with-top-bar) {
    margin-left: var(--header-width);
    margin-top: 0;
  }
  &.with-top-bar.with-header {
    margin-top: 8vh;
    margin-left: 0;
  }
  &.with-top-bar:not(.with-header) {
    margin-top: 0;
    margin-left: 0;
  }
}

.fade-header-enter-active,
.fade-header-leave-active {
  transition: opacity 0.6s cubic-bezier(0.4, 0, 0.2, 1), transform 0.6s cubic-bezier(0.4, 0, 0.2, 1);
  will-change: opacity, transform;
}
.fade-header-enter-from,
.fade-header-leave-to {
  opacity: 0;
  transform: scale(0.98);
}
.fade-header-enter-to,
.fade-header-leave-from {
  opacity: 1;
  transform: scale(1);
}
</style>
