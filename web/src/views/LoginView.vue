<template>
  <!-- TODO 登录页面样式优化；（1）风格和整个系统统一； -->
  <div class="login-view" :style="{ backgroundImage: `url(${loginBg})` }" :class="{ 'has-alert': serverStatus === 'error' }">
    <!-- 服务状态提示 -->
    <div v-if="serverStatus === 'error'" class="server-status-alert">
      <div class="alert-content">
        <exclamation-circle-outlined class="alert-icon" />
        <div class="alert-text">
          <div class="alert-title">服务端连接失败</div>
          <div class="alert-message">{{ serverError }}</div>
        </div>
        <a-button type="link" size="small" @click="checkServerHealth" :loading="healthChecking">
          重试
        </a-button>
      </div>
    </div>

    <div class="login-container">
      <div class="login-logo">
        <img src="/fire.svg" alt="Logo" v-if="false" />
        <h1>智能文档助手</h1>
      </div>

      <!-- 初始化管理员表单 -->
      <div v-if="isFirstRun" class="login-form">
        <h2>系统初始化</h2>
        <p class="init-desc">系统首次运行，请创建超级管理员账户：</p>

        <a-form
          :model="adminForm"
          @finish="handleInitialize"
          layout="vertical"
        >
          <a-form-item
            label="用户名"
            name="username"
            :rules="[{ required: true, message: '请输入用户名' }]"
          >
            <a-input v-model:value="adminForm.username" prefix-icon="user" />
          </a-form-item>

          <a-form-item
            label="密码"
            name="password"
            :rules="[{ required: true, message: '请输入密码' }]"
          >
            <a-input-password v-model:value="adminForm.password" prefix-icon="lock" />
          </a-form-item>

          <a-form-item
            label="确认密码"
            name="confirmPassword"
            :rules="[
              { required: true, message: '请确认密码' },
              { validator: validateConfirmPassword }
            ]"
          >
            <a-input-password v-model:value="adminForm.confirmPassword" prefix-icon="lock" />
          </a-form-item>

          <a-form-item>
            <a-button type="primary" html-type="submit" :loading="loading" block>创建管理员账户</a-button>
          </a-form-item>
        </a-form>
      </div>

      <!-- 登录表单 -->
      <div v-else class="login-form">
        <!-- <h2>用户登录</h2> -->
        <!-- @finishd表单验证通过，html-type="submit" 的按钮点击时会自动调用 -->
        <a-form
          :model="loginForm"
          @finish="handleLogin"
          layout="vertical"
        >
          <a-form-item
            label="用户名"
            name="username"
            :rules="[{ required: true, message: '请输入用户名' }]"
          >
            <a-input v-model:value="loginForm.username">
              <template #prefix>
                <user-outlined />
              </template>
            </a-input>
          </a-form-item>

          <a-form-item
            label="密码"
            name="password"
            :rules="[{ required: true, message: '请输入密码' }]"
          >
            <a-input-password v-model:value="loginForm.password">
              <template #prefix>
                <lock-outlined />
              </template>
            </a-input-password>
          </a-form-item>

          <a-form-item>
            <div class="login-options">
              <a-checkbox v-model:checked="rememberMe" @click="showDevMessage">记住我</a-checkbox>
              <a class="forgot-password" @click="showDevMessage">忘记密码?</a>
            </div>
          </a-form-item>

          <a-form-item>
            <a-button type="primary" html-type="submit" :loading="loading" block style="height: 40px;">账号密码登录</a-button>
          </a-form-item>

          <!-- 第三方登录选项 -->
          <div class="third-party-login">
            <div class="divider">
              <span>其他登录方式</span>
            </div>
            <div class="login-icons">
              <a-tooltip title="微信登录">
                <a-button shape="circle" class="login-icon" @click="showDevMessage">
                  <template #icon><wechat-outlined /></template>
                </a-button>
              </a-tooltip>
              <a-tooltip title="企业微信登录">
                <a-button shape="circle" class="login-icon" @click="showDevMessage">
                  <template #icon><qrcode-outlined /></template>
                </a-button>
              </a-tooltip>
              <a-tooltip title="飞书登录">
                <a-button shape="circle" class="login-icon" @click="showDevMessage">
                  <template #icon><thunderbolt-outlined /></template>
                </a-button>
              </a-tooltip>
            </div>
          </div>
        </a-form>
      </div>

      <!-- 错误提示 -->
      <div v-if="errorMessage" class="error-message">
        {{ errorMessage }}
      </div>

      <!-- 页脚 -->
      <div class="login-footer">
        <a @click="showDevMessage">联系我们</a>
        <a @click="showDevMessage">使用帮助</a>
        <a @click="showDevMessage">隐私政策</a>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useUserStore } from '@/stores/user';
import { message } from 'ant-design-vue';
import { chatApi } from '@/apis/auth_api';
import { authApi, healthApi } from '@/apis/public_api';
import { UserOutlined, LockOutlined, WechatOutlined, QrcodeOutlined, ThunderboltOutlined, ExclamationCircleOutlined } from '@ant-design/icons-vue';
import loginBg from '@/assets/pics/login_bg.jpg';

const router = useRouter();
const userStore = useUserStore();

// 状态
const isFirstRun = ref(false);
const loading = ref(false);
const casLoading = ref(false);
const errorMessage = ref('');
const rememberMe = ref(false);
const serverStatus = ref('loading');
const serverError = ref('');
const healthChecking = ref(false);

// 登录表单
const loginForm = reactive({
  //username: 'liuxu',
  //password: '123456'
   username: '',
   password: ''
});

// 管理员初始化表单
const adminForm = reactive({
  username: '',
  password: '',
  confirmPassword: ''
});

// 开发中功能提示
const showDevMessage = () => {
  message.warning('该功能正在开发中，敬请期待！');
};

// 密码确认验证
const validateConfirmPassword = (rule, value) => {
  if (value === '') {
    return Promise.reject('请确认密码');
  }
  if (value !== adminForm.password) {
    return Promise.reject('两次输入的密码不一致');
  }
  return Promise.resolve();
};

// 处理登录
const handleLogin = async () => {
  try {
    loading.value = true;
    errorMessage.value = '';

    await userStore.login({
      username: loginForm.username,
      password: loginForm.password
    });

    message.success('登录成功');
    await redirectAfterLogin();
  } catch (error) {
    console.error('登录失败:', error);
    errorMessage.value = error.message || '登录失败，请检查用户名和密码';
  } finally {
    loading.value = false;
  }
};

// 处理CAS登录

// 登录后重定向
const redirectAfterLogin = async () => {
  // 获取重定向路径
  const redirectPath = sessionStorage.getItem('redirect') || '/';
  sessionStorage.removeItem('redirect'); // 清除重定向信息

  // 根据用户角色决定重定向目标
  if (redirectPath === '/') {
    // 如果是管理员，直接跳转到/chat页面
    if (userStore.isAdmin) {
      router.push('/chat');
      return;
    }

    // 普通用户跳转到默认智能体
    try {
      // 尝试获取默认智能体
      const data = await chatApi.getDefaultAgent();
      if (data.default_agent_id) {
        // 如果存在默认智能体，直接跳转
        router.push(`/agent/${data.default_agent_id}`);
        return;
      }

      // 没有默认智能体，获取第一个可用智能体
      const agentData = await chatApi.getAgents();
      if (agentData.agents && agentData.agents.length > 0) {
        router.push(`/agent/${agentData.agents[0].name}`);
        return;
      }

      // 没有可用智能体，回退到首页
      router.push('/');
    } catch (error) {
      console.error('获取智能体信息失败:', error);
      router.push('/');
    }
  } else {
    // 跳转到其他预设的路径
    router.push(redirectPath);
  }
};

// 处理初始化管理员
const handleInitialize = async () => {
  try {
    loading.value = true;
    errorMessage.value = '';

    if (adminForm.password !== adminForm.confirmPassword) {
      errorMessage.value = '两次输入的密码不一致';
      return;
    }

    await userStore.initialize({
      username: adminForm.username,
      password: adminForm.password
    });

    message.success('管理员账户创建成功');
    router.push('/');
  } catch (error) {
    console.error('初始化失败:', error);
    errorMessage.value = error.message || '初始化失败，请重试';
  } finally {
    loading.value = false;
  }
};

// 检查是否是首次运行
const checkFirstRunStatus = async () => {
  try {
    loading.value = true;
    const isFirst = await userStore.checkFirstRun();
    isFirstRun.value = isFirst;
  } catch (error) {
    console.error('检查首次运行状态失败:', error);
    errorMessage.value = '系统出错，请稍后重试';
  } finally {
    loading.value = false;
  }
};

// 检查服务器健康状态
const checkServerHealth = async () => {
  try {
    healthChecking.value = true;
    const response = await healthApi.check();
    if (response.status === 'ok') {
      serverStatus.value = 'ok';
    } else {
      serverStatus.value = 'error';
      serverError.value = response.message || '服务端状态异常';
    }
  } catch (error) {
    console.error('检查服务器健康状态失败:', error);
    serverStatus.value = 'error';
    serverError.value = error.message || '无法连接到服务端，请检查网络连接';
  } finally {
    healthChecking.value = false;
  }
};

// 处理CAS重定向（安全方案）
const handleCasRedirect = async () => {
  try {
    loading.value = true;
    // 从URL参数中获取CAS重定向信息
    const urlParams = new URLSearchParams(window.location.search);
    const sessionToken = urlParams.get('session_token');

    if (sessionToken) {
      console.log('检测到CAS重定向登录成功（安全方案）');

      // 使用session token交换用户信息
      try {
        const response = await fetch(`/api/auth/cas/exchange?session_token=${sessionToken}`, {
          headers: {
            'Content-Type': 'application/json'
          },
          credentials: 'include'  // 包含Cookie
        });

        if (response.ok) {
          const userData = await response.json();
          console.log('Session token验证成功，用户信息:', userData);

          // 如果返回了 access_token，则保存到 userStore 与 localStorage，以标记为已登录
          if (userData.access_token) {
            try {
              userStore.token = userData.access_token;
              userStore.role = userData.role;
              localStorage.setItem('user_token', userData.access_token);
            } catch (e) {
              console.warn('保存 access_token 失败:', e);
            }
          }

          // 更新用户状态
          userStore.userId = parseInt(userData.user_id || '0');
          userStore.username = userData.username || '';
          userStore.userRole = userData.role || '';

          // 保存用户基本信息到本地存储
          localStorage.setItem('user_id', userData.user_id || '');
          localStorage.setItem('username', userData.username || '');
          localStorage.setItem('user_role', userData.role || '');

          // 保存CAS用户标识
          if (userData.is_cas_user) {
            localStorage.setItem('is_cas_user', 'true');
          }

          message.success('统一身份认证登录成功');
        } else {
          const errorData = await response.json();
          throw new Error(errorData.detail || '登录状态验证失败');
        }
      } catch (error) {
        console.error('Session token验证失败:', error);
        throw new Error('统一身份认证登录失败，请重试');
      }

      // 清除URL参数
      const newUrl = window.location.pathname;
      window.history.replaceState({}, document.title, newUrl);

      await redirectAfterLogin();
      return true;
    }

    return false;
  } catch (error) {
    console.error('CAS重定向处理失败:', error);
    errorMessage.value = error.message || 'CAS登录处理失败';
    return false;
  } finally {
    loading.value = false;
  }
};

// 组件挂载时
onMounted(async () => {
  // 如果已登录，跳转到首页
  if (userStore.isLoggedIn) {
    router.push('/');
    return;
  }

  // 首先处理CAS重定向
  const handledCasRedirect = await handleCasRedirect();
  if (handledCasRedirect) {
    return;
  }

  // 首先检查服务器健康状态
  await checkServerHealth();

  // 检查是否是首次运行
  await checkFirstRunStatus();
});
</script>

<style lang="less" scoped>
.login-view {
  height: 100vh;
  width: 100%;
  display: flex;
  justify-content: center;
  align-items: center;
  background-size: cover;
  background-position: center;
  position: relative;
  padding-top: 0;

  &.has-alert {
    padding-top: 60px;
  }

  &::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.1);
    z-index: 0;
  }
}

.login-container {
  width: 420px;
  padding: 40px;
  background-color: rgba(255, 255, 255, 0.5);
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
  position: relative;
  z-index: 1;
  backdrop-filter: blur(5px);
  // border: 2px solid white;
}

.login-logo {
  text-align: center;

  img {
    height: 50px;
    margin-bottom: 16px;
  }

  h1 {
    font-family: cursive, sans-serif;
    font-size: 48px;
    font-weight: 500;
    color: var(--main-700);
    margin: 0;
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
  }
}

.login-form {
  h2 {
    text-align: center;
    margin-bottom: 30px;
    font-size: 22px;
    font-weight: 500;
    color: #333;
  }

  :deep(.ant-form-item) {
    margin-bottom: 20px;
  }

  :deep(.ant-input-affix-wrapper) {
    padding: 10px 11px;
    height: auto;
  }

  :deep(.ant-btn) {
    font-size: 16px;
  }
}

.login-options {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;

  .forgot-password {
    color: #9b0505;
    font-size: 14px;

    &:hover {
      color: #40a9ff;
    }
  }
}

.init-desc {
  margin-bottom: 24px;
  color: #666;
  text-align: center;
}

.error-message {
  margin-top: 16px;
  padding: 10px 12px;
  background-color: #fff2f0;
  border: 1px solid #ffccc7;
  border-radius: 4px;
  color: #ff4d4f;
  font-size: 14px;
}

.third-party-login {
  margin-top: 20px;

  .divider {
    position: relative;
    text-align: center;
    margin: 16px 0;

    &::before, &::after {
      content: '';
      position: absolute;
      top: 50%;
      width: calc(50% - 60px);
      height: 1px;
      background-color: #e8e8e8;
    }

    &::before {
      left: 0;
    }

    &::after {
      right: 0;
    }

    span {
      display: inline-block;
      padding: 0 12px;
      // background-color: #fff;
      position: relative;
      color: #ffffff;
      font-size: 14px;
    }
  }

  .login-icons {
    display: flex;
    justify-content: center;
    margin-top: 16px;

    .login-icon {
      margin: 0 12px;
      width: 42px;
      height: 42px;
      color: #666;
      border: 1px solid var(--gray-300);
      transition: all 0.3s;

      &:hover {
        color: var(--main-color);
        border-color: var(--main-color);
      }
    }
  }
}

.login-footer {
  margin-top: 24px;
  text-align: center;
  font-size: 13px;

  a {
    color: #666;
    margin: 0 8px;
    cursor: pointer;

    &:hover {
      color: var(--main-color);
    }
  }
}

.server-status-alert {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  padding: 12px 20px;
  background: linear-gradient(135deg, #ff4d4f, #ff7875);
  color: white;
  z-index: 1000;
  box-shadow: 0 2px 8px rgba(255, 77, 79, 0.3);

  .alert-content {
    display: flex;
    align-items: center;
    max-width: 1200px;
    margin: 0 auto;

    .alert-icon {
      font-size: 20px;
      margin-right: 12px;
      color: white;
    }

    .alert-text {
      flex: 1;

      .alert-title {
        font-weight: 600;
        font-size: 16px;
        margin-bottom: 2px;
      }

      .alert-message {
        font-size: 14px;
        opacity: 0.9;
      }
    }

    :deep(.ant-btn-link) {
      color: white;
      border-color: white;

      &:hover {
        color: white;
        background-color: rgba(255, 255, 255, 0.1);
      }
    }
  }
}
</style>
