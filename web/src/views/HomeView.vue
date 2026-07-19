<template>
  <div class="home-container">
    <div class="hero-section">
      <div class="glass-header">
        <div class="logo">
          <img src="/logo_03.png" alt="山海" class="logo-img" />
          <!-- <span style="font-size: 1.3rem; font-weight: bold;">&nbsp;&nbsp;&nbsp;山海党建管理大模型</span> -->
        </div>
      </div>

      <div class="hero-content">
        <h1 class="title fade-in-up" :style="{ animationDelay: '0.2s' }">{{ title }}</h1>
        <!-- <div class="description">
          <p class="subtitle"></p>
          <p class="features">
            <span>🔍 AI联网搜索</span>
            <span>⌛️ 数海淘沙</span>
            <span>✍ 写作达人</span>
            <span>📚 灵活知识库</span>
          </p>
          <p class="features">
            <span>🚩 党建决策引导</span>
            <span>📚 党建题目生成</span>
            <span>🕸️ 知识图谱集成</span>
          </p>
        </div> -->
        <div class="description fade-in-up" :style="{ animationDelay: '0.4s' }">
          <p class="subtitle"></p>
          <p class="features">
            <span class="fade-in-up" :style="{ animationDelay: '0.5s' }">🔍&nbsp;&nbsp;AI联网搜索</span>
            <span class="fade-in-up" :style="{ animationDelay: '0.6s' }">⌛️&nbsp;&nbsp;数海淘沙</span>
            <span class="fade-in-up" :style="{ animationDelay: '0.7s' }">✍&nbsp;&nbsp;写作达人</span>
            <span class="fade-in-up" :style="{ animationDelay: '0.8s' }">📚&nbsp;&nbsp;灵活知识库</span>
          </p>
          <p class="features">
            <span class="fade-in-up" :style="{ animationDelay: '0.9s' }">🚩&nbsp;&nbsp;党建决策引导</span>
            <span class="fade-in-up" :style="{ animationDelay: '1.0s' }">📜&nbsp;&nbsp;党建题目生成</span>
            <span class="fade-in-up" :style="{ animationDelay: '1.1s' }">🕸️&nbsp;&nbsp;知识图谱集成</span>
          </p>
        </div>
        <button class="start-button fade-in-up" :style="{ animationDelay: '1.3s' }" @click="goToChat">
          开始体验
          <span class="light-dot"></span>
        </button>
        
        <!-- 下滑提示 -->
        <div class="scroll-down-hint">
          <div class="scroll-text">下滑查看更多</div>
          <div class="scroll-arrow">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M12 5V19M12 19L19 12M12 19L5 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </div>
        </div>
      </div>
    </div>

    <div class="preview-section">
      <div class="preview-content">
        <div class="preview-image">
          <img src="/intro/writer.png" alt="智能写作助手图片" />
        </div>
        <div class="preview-text">
          <h3>智能写作助手</h3>
          <p>自动生成党建相关文档和报告。</p>
        </div>
      </div>
    </div>

    <div class="preview-section">
      <div class="preview-content">
        <div class="preview-image">
          <img src="/intro/sand.png" alt="数海淘沙图片" />
        </div>
        <div class="preview-text">
          <h3>数海淘沙</h3>
          <p>从海量数据中快速提炼价值信息。</p>
        </div>
      </div>
    </div>

    <div class="preview-section">
      <div class="preview-content">
        <div class="preview-image">
          <img src="/intro/guide.png" alt="决策引导模块图片" />
        </div>
        <div class="preview-text">
          <h3>决策引导</h3>
          <p>基于大模型智能分析，辅助党建领域精准决策。</p>
        </div>
      </div>
    </div>

    <div class="preview-section">
      <div class="preview-content">
        <div class="preview-image">
          <img src="/intro/study.png" alt="题目生成图片" />
        </div>
        <div class="preview-text">
          <h3>题目生成</h3>
          <p>智能生成个性化试题，高效助力学习评估。</p>
        </div>
      </div>
    </div>

    <div class="preview-section">
      <div class="preview-content">
        <div class="preview-image">
          <img src="/intro/graph.png" alt="知识图谱可视化图片" />
        </div>
        <div class="preview-text">
          <h3>知识图谱可视化</h3>
          <p>直观展示党建知识关联，辅助决策分析。</p>
        </div>
      </div>
    </div>

    <div class="preview-section">
      <div class="preview-content">
        <div class="preview-image">
          <img src="/intro/search.png" alt="大模型联网搜索图片" />
        </div>
        <div class="preview-text">
          <h3>大模型联网搜索</h3>
          <p>快速检索海量党建知识，精准定位所需内容。</p>
        </div>
      </div>
    </div>

    <div class="preview-section">
      <div class="preview-content">
        <div class="preview-image">
          <img src="/intro/deepseek.png" alt="中石大-DeepSeek协作图片" />
        </div>
        <div class="preview-text">
          <h3>中石大-DeepSeek</h3>
          <p>联合中石大DeepSeek，共建党建智能应用体系。</p>
        </div>
      </div>
    </div>


    <footer>
      <p style="margin: 2px;">@ 中国石油大学（北京）@ 2025 技术支持: 信息化管理处、人工智能学院</p>
    </footer>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { chatApi } from '@/apis/auth_api'

const title = ref('钻采工程智能助手')
const router = useRouter()
const userStore = useUserStore()
const currentHoverIndex = ref(0)
let hoverInterval = null

const goToChat = async () => {
  // 检查用户是否登录
  if (!userStore.isLoggedIn) {
    // 登录后应该跳转到默认智能体而不是/agent
    sessionStorage.setItem('redirect', '/');  // 设置为首页，登录后会通过路由守卫处理重定向
    router.push('/login');
    return;
  }

  // 根据用户角色进行跳转
  if (userStore.isAdmin) {
    // 管理员用户跳转到聊天页面
    router.push('/chat');
    return;
  }

  // 普通用户跳转到默认智能体
  try {
    // 获取默认智能体
    const data = await chatApi.getDefaultAgent();
    if (data.default_agent_id) {
      // 使用后端设置的默认智能体
      router.push(`/agent/${data.default_agent_id}`);
    } else {
      // 如果没有设置默认智能体，则获取智能体列表选择第一个
      const agentData = await chatApi.getAgents();
      if (agentData.agents && agentData.agents.length > 0) {
        router.push(`/agent/${agentData.agents[0].name}`);
      } else {
        // 没有可用智能体，回退到chat页面
        router.push("/chat");
      }
    }
  } catch (error) {
    console.error('跳转到智能体页面失败:', error);
    router.push("/chat");
  }
};

const startHoverAnimation = () => {
  const featureSpans = document.querySelectorAll('.features span')
  if (featureSpans.length === 0) return

  hoverInterval = setInterval(() => {
    // 移除所有元素的hover类
    featureSpans.forEach(span => span.classList.remove('auto-hover'))
    
    // 为当前索引的元素添加hover类
    featureSpans[currentHoverIndex.value].classList.add('auto-hover')
    
    // 更新索引
    currentHoverIndex.value = (currentHoverIndex.value + 1) % featureSpans.length
  }, 2000) // 每2秒切换一次
}

const stopHoverAnimation = () => {
  if (hoverInterval) {
    clearInterval(hoverInterval)
    hoverInterval = null
  }
  
  // 移除所有hover效果
  const featureSpans = document.querySelectorAll('.features span')
  featureSpans.forEach(span => span.classList.remove('auto-hover'))
}

onMounted(() => {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
      } else {
        entry.target.classList.remove('visible');
      }
    });
  }, {
    threshold: 0.1,
    rootMargin: '-50px 0px -100px 0px'
  });

  const sections = document.querySelectorAll('.preview-section');
  sections.forEach(section => {
    observer.observe(section);
  });

  // 启动自动hover动画
  setTimeout(() => {
    startHoverAnimation()
  }, 1000)

  return () => {
    sections.forEach(section => {
      observer.unobserve(section);
    });
    stopHoverAnimation()
  };
});

onUnmounted(() => {
  stopHoverAnimation()
})
</script>

<style lang="less" scoped>
.home-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  color: #333;
  // background: linear-gradient(135deg, #f5f7fa, #e6eaef, #f0f4f8, #eef2f7);
  background-size: 400% 400%;
  animation: gradientBackground 15s ease infinite;
  overflow-x: hidden; // 移除横向滚动
}

@keyframes gradientBackground {
  0% {
    background-position: 0% 50%;
  }
  50% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0% 50%;
  }
}

.glass-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  padding: 1.2rem 2rem;
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.3);
  box-shadow: 0 4px 30px rgba(0, 0, 0, 0.1);
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
  transition: all 0.3s ease;

  &:hover {
    background: rgba(255, 255, 255, 0.25);
  }
}

.logo {
  display: flex;
  align-items: center;
  font-size: 1.4rem;
  font-weight: bold;
  color: var(--main-color, #333);

  .logo-img {
    height: 2.5rem;
    margin-right: 0.6rem;
  }
}


.hero-section {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  text-align: center;
  padding: 6rem 2rem 2rem;
}

.hero-content {
  max-width: 800px;
}

.title {
  position: relative;
  font-size: 4.2rem;
  font-weight: 700;
  margin-bottom: 2.5rem;
  background: linear-gradient(45deg, #ff0000, #910000);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  text-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
  
  &::before {
    content: '';
    position: absolute;
    top: 0;
    left: -100%;
    width: 20%;
    height: 100%;
    background: linear-gradient(90deg, 
      rgba(255, 255, 255, 0.5),
    );
    animation: glassSlide 3s ease-in-out infinite;
    z-index: 1;
    filter: contrast(1.5) brightness(1.2);
    transform: skewX(-15deg);
  }
}

@keyframes glassSlide {
  0% {
    left: -100%;
  }
  100% {
    left: 200%;
  }
}

.description {
  margin-bottom: 2.5rem;
}

.subtitle {
  font-size: 1.5rem;
  font-weight: 500;
  margin-bottom: 1.5rem;
  color: #555;
}

.features {
  display: flex;
  justify-content: center;
  gap: 1.5rem;
  font-size: 1.1rem;
  margin-bottom: 1rem;

  span {
    position: relative;
    padding: 0.8rem 1.5rem;
    background: linear-gradient(135deg, rgba(255, 255, 255, 0.9), rgba(245, 245, 245, 0.9));
    border-radius: 2.5rem;
    box-shadow: 0 4px 15px rgba(0, 0, 0, 0.08),
                0 2px 4px rgba(0, 0, 0, 0.05),
                inset 0 1px 2px rgba(255, 255, 255, 0.8);
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    border: 1px solid rgba(255, 255, 255, 0.5);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    font-weight: 500;
    color: #444;
    
    &::before {
      content: '';
      position: absolute;
      top: -2px;
      left: -2px;
      right: -2px;
      bottom: -2px;
      background: linear-gradient(45deg, 
        rgba(255, 0, 0, 0.1), 
        rgba(200, 0, 0, 0.05), 
        rgba(255, 0, 0, 0.1)
      );
      border-radius: 2.8rem;
      z-index: -1;
      opacity: 0;
      transition: opacity 0.3s ease;
    }
  }

  span:hover {
    transform: translateY(-3px) scale(1.05);
    background: linear-gradient(135deg, rgba(255, 255, 255, 1), rgba(250, 250, 250, 0.95));
    box-shadow: 0 8px 25px rgba(0, 0, 0, 0.12),
                0 4px 8px rgba(0, 0, 0, 0.08),
                inset 0 2px 4px rgba(255, 255, 255, 0.9);
    color: #222;
    
    &::before {
      opacity: 1;
    }
  }

  span:active {
    transform: translateY(-1px) scale(1.02);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1),
                0 2px 4px rgba(0, 0, 0, 0.06),
                inset 0 1px 2px rgba(255, 255, 255, 0.8);
  }

  /* 自动hover效果 */
  span.auto-hover {
    transform: translateY(-3px) scale(1.05);
    background: linear-gradient(135deg, rgba(255, 255, 255, 1), rgba(250, 250, 250, 0.95));
    box-shadow: 0 8px 25px rgba(0, 0, 0, 0.12),
                0 4px 8px rgba(0, 0, 0, 0.08),
                inset 0 2px 4px rgba(255, 255, 255, 0.9);
    color: #222;
    
    &::before {
      opacity: 1;
    }
  }
}

  .start-button {
    position: relative;
    padding: 1rem 3rem;
    font-size: 1.2rem;
    font-weight: 600;
    color: white;
    background: linear-gradient(135deg, #fd2c2c, #ab0202);
    border: none;
    border-radius: 3rem;
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: 
      0 4px 15px rgba(255, 0, 0, 0.2),
      0 1px 3px rgba(0, 0, 0, 0.1),
      inset 0 1px 2px rgba(255, 255, 255, 0.3);
    overflow: hidden;
    z-index: 1;

    &::before {
      content: '';
      position: absolute;
      top: 0;
      left: -100%;
      width: 100%;
      height: 100%;
      background: linear-gradient(
        90deg,
        rgba(255, 255, 255, 0) 0%,
        rgba(255, 255, 255, 0.3) 50%,
        rgba(255, 255, 255, 0) 100%
      );
      transition: left 0.5s ease;
      z-index: 1;
    }

    /* 光点元素 */
    .light-dot {
      position: absolute;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: linear-gradient(135deg, #ffffff, #ffdddd);
      box-shadow: 0 0 12px 3px rgba(255, 255, 255, 0.9);
      animation: dotTravel 3s ease-in-out infinite;
      z-index: 2;
    }

    &:hover {
      transform: translateY(-4px) scale(1.05);
      background: linear-gradient(135deg, #ff5252, #ff0000);
      box-shadow: 
        0 8px 25px rgba(255, 0, 0, 0.35),
        0 4px 8px rgba(0, 0, 0, 0.15),
        inset 0 2px 4px rgba(255, 255, 255, 0.4);
      
      &::before {
        left: 100%;
      }
      
      .light-dot {
        animation: dotTravel 3s ease-in-out infinite;
        box-shadow: 0 0 18px 4px rgba(255, 255, 255, 1);
        background: linear-gradient(135deg, #ffffff, #ffeeee);
      }
    }

    &:active {
      transform: translateY(-1px) scale(1.02);
      box-shadow: 
        0 5px 15px rgba(255, 0, 0, 0.25),
        0 2px 4px rgba(0, 0, 0, 0.1),
        inset 0 1px 2px rgba(255, 255, 255, 0.3);
    }
  }

@keyframes dotTravel {
  0% {
    top: -4px;
    left: 0%;
    transform: translateX(-50%);
  }
  25% {
    top: -4px;
    left: 100%;
    transform: translateX(-50%);
  }
  50% {
    top: 100%;
    left: 100%;
    transform: translate(-50%, -100%);
  }
  75% {
    top: 100%;
    left: 0%;
    transform: translate(-50%, -100%);
  }
  100% {
    top: -4px;
    left: 0%;
    transform: translateX(-50%);
  }
}

.preview-section {
  padding: 5rem 2rem;
  margin-bottom: 3rem;
  transition: all 0.5s cubic-bezier(0.4, 0, 0.2, 1);
  opacity: 0;
  filter: blur(8px);
  
  // 奇数项从左侧入场
  &:nth-child(odd) {
    background: rgba(255,255,255,0.03);
    transform: translateX(-100px) scale(0.95);
    
    &.visible {
      transform: translateX(0) scale(1);
      opacity: 1;
      filter: blur(0);
    }
  }
  
  // 偶数项从右侧入场
  &:nth-child(even) {
    transform: translateX(100px) scale(0.95);
    
    &.visible {
      transform: translateX(0) scale(1);
      opacity: 1;
      filter: blur(0);
    }
  }
  
  // 交错动画延迟
  &:nth-child(2) {
    transition-delay: 0.1s;
  }
  &:nth-child(3) {
    transition-delay: 0.2s;
  }
  &:nth-child(4) {
    transition-delay: 0.3s;
  }
  &:nth-child(5) {
    transition-delay: 0.4s;
  }
  &:nth-child(6) {
    transition-delay: 0.5s;
  }
  &:nth-child(7) {
    transition-delay: 0.6s;
  }
  &:nth-child(8) {
    transition-delay: 0.7s;
  }
}

.preview-content {
  display: flex;
  align-items: center;
  max-width: 1200px;
  margin: 0 auto;
  gap: 4rem;
}

.preview-section:nth-child(even) .preview-content {
  flex-direction: row-reverse;
}

.preview-image {
  flex: 6;
  border-radius: 1rem;
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
  transition: all 0.3s ease;

  img {
    width: 100%;
    height: auto;
    display: block;
    transition: all 0.3s ease;
    
  }

  &:hover {
    box-shadow: 0 15px 40px rgba(0, 0, 0, 0.15);
    transform: translateY(-5px);
    transform: scale(1.1);
  }
}

.preview-text {
  flex: 1.6;
  padding: 2rem;
  transition: all 0.5s ease;

  h3 {
    font-size: 2rem;
    margin-bottom: 1.5rem;
    color: #333;
    font-weight: 600;
    position: relative;
    padding-bottom: 1rem;

    &::after {
      content: '';
      position: absolute;
      bottom: 0;
      left: 0;
      width: 60px;
      height: 3px;
      background: linear-gradient(90deg, #ff0000, #540000);
    }
  }

  p {
    font-size: 1.1rem;
    line-height: 1.8;
    color: #555;
  }
}

@media (max-width: 768px) {
  .preview-content {
    flex-direction: column;
    gap: 2rem;
  }

  .preview-text {
    padding: 1rem 0;
    text-align: center;

    h3::after {
      left: 50%;
      transform: translateX(-50%);
    }
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(40px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 渐入动画效果 */
.fade-in-up {
  opacity: 0;
  transform: translateY(30px);
  animation: fadeInUp 0.8s ease forwards;
}

/* 下滑提示样式 */
.scroll-down-hint {
  margin-top: 3rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  opacity: 0;
  animation: fadeInUp 1s ease 1s forwards;
}

.scroll-text {
  font-size: 1rem;
  color: #666;
  margin-bottom: 0.8rem;
  font-weight: 500;
}

.scroll-arrow {
  animation: bounce 2s infinite;
  color: #999;
}

@keyframes bounce {
  0%, 20%, 50%, 80%, 100% {
    transform: translateY(0);
  }
  40% {
    transform: translateY(-10px);
  }
  60% {
    transform: translateY(-5px);
  }
}

footer {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  text-align: center;
  padding: 0.5rem;
  color: #666;
  font-size: 0.9rem;
  background: rgba(231, 231, 231, 0.382);
  backdrop-filter: blur(5px);
}

@media (max-width: 768px) {
  .glass-header {
    padding: 1rem;
  }

  .logo {
    font-size: 1.2rem;
  }

  .title {
    font-size: 2.5rem;
  }

  .subtitle {
    font-size: 1.2rem;
  }

  .features {
    flex-direction: column;
    gap: 0.8rem;
    
    span {
      padding: 0.6rem 1.2rem;
      font-size: 1rem;
      backdrop-filter: blur(5px);
      -webkit-backdrop-filter: blur(5px);
    }
  }

  .start-button {
    padding: 0.8rem 2rem;
    font-size: 1rem;
  }

  .preview-section {
    padding: 3rem 1rem;
  }

  .scroll-down-hint {
    margin-top: 2rem;
  }
  
  .scroll-text {
    font-size: 0.9rem;
  }
  
  .scroll-arrow svg {
    width: 20px;
    height: 20px;
  }
}
</style>
