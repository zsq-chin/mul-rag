<template>
  <div class="cyber-container">
    <!-- 黑客代码流 -->
    <div class="code-stream left-stream" ref="leftStream"></div>
    <div class="code-stream right-stream" ref="rightStream"></div>
    
    <!-- 粒子背景 -->
    <div class="particles"></div>
    
    <!-- 主内容 -->
      <div class="cyber-content">
      <h1 class="cyber-title" data-text="404">404</h1>
      <h2 class="cyber-subtitle">来不及了，放开玩吧</h2>
      <p class="cyber-text">Sorry.liuxu has not been zuoed</p>
      
      <!-- 交互式按钮liuxu has not been zuoed -->
      <div class="cyber-buttons">
        <button class="cyber-button" @click="redirectHome">
          <span class="glitch-text">返回基地</span>
          <span class="cyber-button__glitch"></span>
          <span class="cyber-button__tag">R25</span>
        </button>
        <button class="cyber-button" @click="randomEffect">
          <span class="glitch-text">随机效果</span>
          <span class="cyber-button__glitch"></span>
          <span class="cyber-button__tag">X7</span>
        </button>
      </div>
    </div>

    <!-- 扫描线效果 -->
    <div class="scanlines"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const colors = ref(['#0f0', '#f0f', '#0ff', '#ff0', '#f00'])
const leftStream = ref(null)
const rightStream = ref(null)

const codeSnippets = [
  "> INITIATING SYSTEM SCAN...",
  "> CONNECTING TO MAINFRAME...",
  "ACCESS CODE: ********",
  "DECRYPTING SECURITY LAYERS...",
  "> BYPASSING FIREWALL... [DONE]",
  "> INJECTING MALICIOUS PAYLOAD...",
  "ESTABLISHING ROOT ACCESS...",
  "WARNING: INTRUSION DETECTED",
  "> ACTIVATING COUNTERMEASURES...",
  "DOWNLOADING SENSITIVE DATA...",
  "> DELETING LOG FILES...",
  "COVERING TRACKS... [COMPLETE]",
  "SYSTEM INTEGRITY COMPROMISED",
  "> MAINFRAME CONTROL OBTAINED",
  "WARNING: ADMIN ALERT TRIGGERED",
  "> INITIATING SELF-DESTRUCT...",
  "COUNTDOWN: 10...9...8...7...",
  "TERMINATING ALL PROCESSES...",
  "SYSTEM SHUTDOWN IMMINENT...",
  "> EMERGENCY OVERRIDE ENGAGED",
  "CRITICAL FAILURE DETECTED",
  "> REBOOTING IN SAFE MODE...",
  "SYSTEM RESTORATION IN PROGRESS",
  "> SCANNING FOR VULNERABILITIES",
  "NEW EXPLOITS IDENTIFIED...",
  "> PREPARING NEXT ATTACK WAVE...",
    "> INITIATING SYSTEM SCAN...",
  "> CONNECTING TO MAINFRAME...",
  "ACCESS CODE: ********",
  "DECRYPTING SECURITY LAYERS...",
  "> BYPASSING FIREWALL... [DONE]",
  "> INJECTING MALICIOUS PAYLOAD...",
  "ESTABLISHING ROOT ACCESS...",
  "WARNING: INTRUSION DETECTED",
  "> ACTIVATING COUNTERMEASURES...",
  "DOWNLOADING SENSITIVE DATA...",
  "> DELETING LOG FILES...",
  "COVERING TRACKS... [COMPLETE]",
  "SYSTEM INTEGRITY COMPROMISED",
  "> MAINFRAME CONTROL OBTAINED",
  "WARNING: ADMIN ALERT TRIGGERED",
  "> INITIATING SELF-DESTRUCT...",
  "COUNTDOWN: 10...9...8...7...",
  "TERMINATING ALL PROCESSES...",
  "SYSTEM SHUTDOWN IMMINENT...",
  "> EMERGENCY OVERRIDE ENGAGED",
  "CRITICAL FAILURE DETECTED",
  "> REBOOTING IN SAFE MODE...",
  "SYSTEM RESTORATION IN PROGRESS",
  "> SCANNING FOR VULNERABILITIES",
  "NEW EXPLOITS IDENTIFIED...",
  "> PREPARING NEXT ATTACK WAVE...",
    "> INITIATING SYSTEM SCAN...",
  "> CONNECTING TO MAINFRAME...",
  "ACCESS CODE: ********",
  "DECRYPTING SECURITY LAYERS...",
  "> BYPASSING FIREWALL... [DONE]",
  "> INJECTING MALICIOUS PAYLOAD...",
  "ESTABLISHING ROOT ACCESS...",
  "WARNING: INTRUSION DETECTED",
  "> ACTIVATING COUNTERMEASURES...",
  "DOWNLOADING SENSITIVE DATA...",
  "> DELETING LOG FILES...",
  "COVERING TRACKS... [COMPLETE]",
  "SYSTEM INTEGRITY COMPROMISED",
  "> MAINFRAME CONTROL OBTAINED",
  "WARNING: ADMIN ALERT TRIGGERED",
  "> INITIATING SELF-DESTRUCT...",
  "COUNTDOWN: 10...9...8...7...",
  "TERMINATING ALL PROCESSES...",
  "SYSTEM SHUTDOWN IMMINENT...",
  "> EMERGENCY OVERRIDE ENGAGED",
  "CRITICAL FAILURE DETECTED",
  "> REBOOTING IN SAFE MODE...",
  "SYSTEM RESTORATION IN PROGRESS",
  "> SCANNING FOR VULNERABILITIES",
  "NEW EXPLOITS IDENTIFIED...",
  "> PREPARING NEXT ATTACK WAVE...",
    "> INITIATING SYSTEM SCAN...",
  "> CONNECTING TO MAINFRAME...",
  "ACCESS CODE: ********",
  "DECRYPTING SECURITY LAYERS...",
  "> BYPASSING FIREWALL... [DONE]",
  "> INJECTING MALICIOUS PAYLOAD...",
  "ESTABLISHING ROOT ACCESS...",
  "WARNING: INTRUSION DETECTED",
  "> ACTIVATING COUNTERMEASURES...",
  "DOWNLOADING SENSITIVE DATA...",
  "> DELETING LOG FILES...",
  "COVERING TRACKS... [COMPLETE]",
  "SYSTEM INTEGRITY COMPROMISED",
  "> MAINFRAME CONTROL OBTAINED",
  "WARNING: ADMIN ALERT TRIGGERED",
  "> INITIATING SELF-DESTRUCT...",
  "COUNTDOWN: 10...9...8...7...",
  "TERMINATING ALL PROCESSES...",
  "SYSTEM SHUTDOWN IMMINENT...",
  "> EMERGENCY OVERRIDE ENGAGED",
  "CRITICAL FAILURE DETECTED",
  "> REBOOTING IN SAFE MODE...",
  "SYSTEM RESTORATION IN PROGRESS",
  "> SCANNING FOR VULNERABILITIES",
  "NEW EXPLOITS IDENTIFIED...",
  "> PREPARING NEXT ATTACK WAVE...",
    "SYSTEM INTEGRITY COMPROMISED",
  "> MAINFRAME CONTROL OBTAINED",
  "WARNING: ADMIN ALERT TRIGGERED",
  "> INITIATING SELF-DESTRUCT...",
  "COUNTDOWN: 10...9...8...7...",
  "TERMINATING ALL PROCESSES...",
  "SYSTEM SHUTDOWN IMMINENT...",
  "> EMERGENCY OVERRIDE ENGAGED",
  "CRITICAL FAILURE DETECTED",
  "> REBOOTING IN SAFE MODE...",
  "SYSTEM RESTORATION IN PROGRESS",
  "> SCANNING FOR VULNERABILITIES",
  "NEW EXPLOITS IDENTIFIED...",
  "> PREPARING NEXT ATTACK WAVE...",
    "> INITIATING SYSTEM SCAN...",
  "> CONNECTING TO MAINFRAME...",
  "ACCESS CODE: ********",
  "DECRYPTING SECURITY LAYERS...",
  "> BYPASSING FIREWALL... [DONE]",
  "> INJECTING MALICIOUS PAYLOAD...",
  "ESTABLISHING ROOT ACCESS...",
  "WARNING: INTRUSION DETECTED",
  "> ACTIVATING COUNTERMEASURES...",
  "DOWNLOADING SENSITIVE DATA...",
  "> DELETING LOG FILES...",
  "COVERING TRACKS... [COMPLETE]",
  "SYSTEM INTEGRITY COMPROMISED",
  "> MAINFRAME CONTROL OBTAINED",
  "WARNING: ADMIN ALERT TRIGGERED",
  "> INITIATING SELF-DESTRUCT...",
  "COUNTDOWN: 10...9...8...7...",
  "TERMINATING ALL PROCESSES...",
  "SYSTEM SHUTDOWN IMMINENT...",
  "> EMERGENCY OVERRIDE ENGAGED",
  "CRITICAL FAILURE DETECTED",
  "> REBOOTING IN SAFE MODE...",
  "SYSTEM RESTORATION IN PROGRESS",
  "> SCANNING FOR VULNERABILITIES",
  "NEW EXPLOITS IDENTIFIED...",
  "> PREPARING NEXT ATTACK WAVE...",
    "> INITIATING SYSTEM SCAN...",
  "> CONNECTING TO MAINFRAME...",
  "ACCESS CODE: ********",
  "DECRYPTING SECURITY LAYERS...",
  "> BYPASSING FIREWALL... [DONE]",
  "> INJECTING MALICIOUS PAYLOAD...",
  "ESTABLISHING ROOT ACCESS...",
  "WARNING: INTRUSION DETECTED",
  "> ACTIVATING COUNTERMEASURES...",
  "DOWNLOADING SENSITIVE DATA...",
  "> DELETING LOG FILES...",
  "COVERING TRACKS... [COMPLETE]",
  "SYSTEM INTEGRITY COMPROMISED",
  "> MAINFRAME CONTROL OBTAINED",
  "WARNING: ADMIN ALERT TRIGGERED",
  "> INITIATING SELF-DESTRUCT...",
  "COUNTDOWN: 10...9...8...7...",
  "TERMINATING ALL PROCESSES...",
  "SYSTEM SHUTDOWN IMMINENT...",
  "> EMERGENCY OVERRIDE ENGAGED",
  "CRITICAL FAILURE DETECTED",
  "> REBOOTING IN SAFE MODE...",
  "SYSTEM RESTORATION IN PROGRESS",
  "> SCANNING FOR VULNERABILITIES",
  "NEW EXPLOITS IDENTIFIED...",
  "> PREPARING NEXT ATTACK WAVE...",
    "> INITIATING SYSTEM SCAN...",
  "> CONNECTING TO MAINFRAME...",
  "ACCESS CODE: ********",
  "DECRYPTING SECURITY LAYERS...",
  "> BYPASSING FIREWALL... [DONE]",
  "> INJECTING MALICIOUS PAYLOAD...",
  "ESTABLISHING ROOT ACCESS...",
  "WARNING: INTRUSION DETECTED",
  "> ACTIVATING COUNTERMEASURES...",
  "DOWNLOADING SENSITIVE DATA...",
  "> DELETING LOG FILES...",
  "COVERING TRACKS... [COMPLETE]",
  "SYSTEM INTEGRITY COMPROMISED",
  "> MAINFRAME CONTROL OBTAINED",
  "WARNING: ADMIN ALERT TRIGGERED",
  "> INITIATING SELF-DESTRUCT...",
  "COUNTDOWN: 10...9...8...7...",
  "TERMINATING ALL PROCESSES...",
  "SYSTEM SHUTDOWN IMMINENT...",
  "> EMERGENCY OVERRIDE ENGAGED",
  "CRITICAL FAILURE DETECTED",
  "> REBOOTING IN SAFE MODE...",
  "SYSTEM RESTORATION IN PROGRESS",
  "> SCANNING FOR VULNERABILITIES",
  "NEW EXPLOITS IDENTIFIED...",
  "> PREPARING NEXT ATTACK WAVE..."
]

onMounted(() => {
  // 确保DOM已加载
  nextTick(() => {
    if (leftStream.value && rightStream.value) {
      initCodeStream(leftStream.value, codeSnippets)
      initCodeStream(rightStream.value, [...codeSnippets].reverse())
    }
  })
})

const initCodeStream = (element, snippets) => {
  try {
    if (!element) return
    
    // 创建代码文本元素
    const codeElement = document.createElement('div')
    codeElement.className = 'code-text'
    Object.assign(codeElement.style, {
      position: 'absolute',
      top: '0',
      left: '0',
      width: '100%',
      color: 'rgba(0, 255, 0, 0.7)',
      fontFamily: "'Courier New', monospace",
      fontSize: '14px',
      lineHeight: '1.5',
      whiteSpace: 'pre',
      padding: '10px'
    })
    
    // 填充代码内容
    codeElement.textContent = snippets.join('\n') + '\n'.repeat(50)
    
    // 清空现有内容并添加新元素
    element.innerHTML = ''
    element.appendChild(codeElement)
    
    // 设置动画
    const animate = () => {
      const currentPos = parseFloat(codeElement.style.transform.replace('translateY(-', '').replace('px)', '')) || 0
      const newPos = (currentPos + 5) % codeElement.scrollHeight
      codeElement.style.transform = `translateY(-${newPos}px)`
      requestAnimationFrame(animate)
    }
    requestAnimationFrame(animate)
  } catch (error) {
    console.error('初始化代码流失败:', error)
  }
}

const redirectHome = () => {
  router.push('/')
}

const randomEffect = () => {
  const randomColor = colors.value[Math.floor(Math.random() * colors.value.length)]
  document.documentElement.style.setProperty('--neon-color', randomColor)
}
</script>

<style scoped>
.cyber-container {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background-color: #0a0a0a;
  overflow: hidden;
  color: var(--neon-color, #0f0);
  font-family: 'Courier New', monospace;
}

.particles {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: radial-gradient(circle, transparent 20%, #0a0a0a 70%);
  z-index: 1;
}

.particles::before {
  content: "";
  position: absolute;
  width: 100%;
  height: 100%;
  background-image: 
    radial-gradient(circle at 20% 30%, var(--neon-color, #0f0) 0.5px, transparent 1px),
    radial-gradient(circle at 80% 70%, var(--neon-color, #0f0) 0.5px, transparent 1px);
  background-size: 50px 50px;
  animation: particleMove 10s linear infinite;
}

@keyframes particleMove {
  100% {
    transform: translate(50px, 50px);
  }
}

.cyber-content {
  position: relative;
  z-index: 2;
  text-align: center;
  padding: 2rem;
  border: 2px solid var(--neon-color, #0f0);
  box-shadow: 0 0 15px var(--neon-color, #0f0);
  background-color: rgba(10, 10, 10, 0.8);
}

.cyber-title {
  font-size: 8rem;
  margin: 0;
  text-shadow: 
    0 0 5px #fff,
    0 0 10px #fff,
    0 0 20px var(--neon-color, #0f0),
    0 0 40px var(--neon-color, #0f0);
  position: relative;
  animation: glitch 2s infinite;
}

.cyber-title::before {
  content: attr(data-text);
  position: absolute;
  left: -2px;
  text-shadow: 2px 0 #f0f;
  clip: rect(44px, 450px, 56px, 0);
  animation: glitch-anim 5s infinite linear alternate-reverse;
}

.cyber-title::after {
  content: attr(data-text);
  position: absolute;
  left: 2px;
  text-shadow: -2px 0 #0ff;
  clip: rect(44px, 450px, 56px, 0);
  animation: glitch-anim2 5s infinite linear alternate-reverse;
}

@keyframes glitch-anim {
  0% { clip: rect(31px, 9999px, 94px, 0); }
  20% { clip: rect(112px, 9999px, 76px, 0); }
  40% { clip: rect(75px, 9999px, 107px, 0); }
  60% { clip: rect(98px, 9999px, 109px, 0); }
  80% { clip: rect(46px, 9999px, 102px, 0); }
  100% { clip: rect(82px, 9999px, 64px, 0); }
}

@keyframes glitch-anim2 {
  0% { clip: rect(65px, 9999px, 119px, 0); }
  20% { clip: rect(25px, 9999px, 145px, 0); }
  40% { clip: rect(112px, 9999px, 59px, 0); }
  60% { clip: rect(98px, 9999px, 140px, 0); }
  80% { clip: rect(38px, 9999px, 85px, 0); }
  100% { clip: rect(15px, 9999px, 37px, 0); }
}

.cyber-subtitle {
  font-size: 2rem;
  margin: 1rem 0;
  text-shadow: 0 0 5px var(--neon-color, #0f0);
  animation: flicker 3s infinite alternate;
}

.cyber-text {
  font-size: 1.5rem;
  margin-bottom: 2rem;
  opacity: 0.8;
}

@keyframes flicker {
  0%, 19%, 21%, 23%, 25%, 54%, 56%, 100% {
    text-shadow:
      0 0 4px #fff,
      0 0 11px #fff,
      0 0 19px #fff,
      0 0 40px var(--neon-color, #0f0),
      0 0 80px var(--neon-color, #0f0),
      0 0 90px var(--neon-color, #0f0),
      0 0 100px var(--neon-color, #0f0),
      0 0 150px var(--neon-color, #0f0);
  }
  20%, 24%, 55% {        
    text-shadow: none;
  }
}

.cyber-buttons {
  display: flex;
  gap: 1rem;
  justify-content: center;
  margin-top: 2rem;
}

.cyber-button {
  position: relative;
  padding: 1rem 2rem;
  color: var(--neon-color, #0f0);
  background: transparent;
  border: 1px solid var(--neon-color, #0f0);
  font-family: 'Courier New', monospace;
  font-size: 1rem;
  text-transform: uppercase;
  letter-spacing: 2px;
  cursor: pointer;
  transition: all 0.3s;
  overflow: hidden;
}

.cyber-button:hover {
  background: rgba(0, 255, 0, 0.1);
  box-shadow: 0 0 10px var(--neon-color, #0f0);
}

.cyber-button__glitch {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: var(--neon-color, #0f0);
  opacity: 0;
  transition: opacity 0.3s;
}

.cyber-button:hover .cyber-button__glitch {
  opacity: 0.1;
}

.cyber-button__tag {
  position: absolute;
  top: -10px;
  right: 5px;
  font-size: 0.7rem;
  color: var(--neon-color, #0f0);
}

.code-stream {
  position: absolute;
  top: 0;
  width: 33vw;
  height: 100%;
  overflow: hidden;
  z-index: 1;
  pointer-events: none;
  background-color: #001a00;
  border-right: 4px solid #00ff00;
  box-shadow: 0 0 30px #00ff00;
}

.right-stream {
  border-right: none;
  border-left: 4px solid #00ff00;
  box-shadow: 0 0 30px #00ff00;
}

.code-text {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  color: #ffffff;
  font-family: 'Courier New', monospace;
  font-size: 22px;
  font-weight: bold;
  line-height: 1.0;
  white-space: pre;
  padding: 30px;
  text-shadow: 0 0 5px #ffffff;
  animation: scrollText 5s linear infinite, pulse 1s infinite alternate;
  background: linear-gradient(to bottom, 
    rgba(0, 255, 0, 0.1) 0%,
    rgba(0, 255, 0, 0.05) 100%);
}

@keyframes scrollText {
  0% { transform: translateY(0); }
  100% { transform: translateY(-100%); }
}

@keyframes pulse {
  0% { text-shadow: 0 0 5px #ffffff; }
  100% { text-shadow: 0 0 15px #ffffff, 0 0 20px #00ff00; }
}

@keyframes streamGlow {
  0% { box-shadow: 0 0 30px #00ff00; }
  50% { box-shadow: 0 0 50px #00ff00; }
  100% { box-shadow: 0 0 30px #00ff00; }
}

.left-stream {
  left: 0;
  background: linear-gradient(90deg, rgba(10,10,10,0.8), transparent);
}

.right-stream {
  right: 0;
  background: linear-gradient(270deg, rgba(10,10,10,0.8), transparent);
}

.code-stream::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 200%;
  background: linear-gradient(
    to bottom,
    transparent 0%,
    var(--neon-color, #0f0) 10%,
    transparent 20%,
    transparent 80%,
    var(--neon-color, #0f0) 90%,
    transparent 100%
  );
  animation: streamFlow 10s linear infinite;
}

@keyframes streamFlow {
  0% { transform: translateY(0); }
  100% { transform: translateY(-50%); }
}

.scanlines {
  position: absolute;
  top: 0;
  left: 200px;
  right: 200px;
  width: 100%;
  height: 100%;
  background: linear-gradient(
    to bottom,
    rgba(0, 255, 0, 0.1) 0%,
    rgba(0, 255, 0, 0) 10%
  );
  background-size: 100% 4px;
  pointer-events: none;
  animation: scanline 8s linear infinite;
  z-index: 3;
}

@keyframes scanline {
  0% { background-position: 0 0; }
  100% { background-position: 0 100%; }
}

.glitch-text {
  position: relative;
  display: inline-block;
}

.glitch-text::before,
.glitch-text::after {
  content: attr(data-text);
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
}

.glitch-text::before {
  color: #f0f;
  z-index: -1;
  animation: glitch-effect 3s infinite;
}

.glitch-text::after {
  color: #0ff;
  z-index: -2;
  animation: glitch-effect 2s infinite reverse;
}

@keyframes glitch-effect {
  0% { transform: translate(0); }
  20% { transform: translate(-3px, 3px); }
  40% { transform: translate(-3px, -3px); }
  60% { transform: translate(3px, 3px); }
  80% { transform: translate(3px, -3px); }
  100% { transform: translate(0); }
}
</style>
