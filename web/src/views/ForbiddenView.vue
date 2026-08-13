<template>
  <div class="forbidden-container">
    <div class="forbidden-card">
      <div class="forbidden-code">403</div>
      <h1 class="forbidden-title">没有权限访问该功能</h1>
      <p class="forbidden-text">
        当前账号无权访问此页面（7.2.2）。如需使用该功能，请联系超级管理员调整角色。
      </p>
      <router-link to="/chat" class="forbidden-back">
        <a-button type="primary">返回智能问答</a-button>
      </router-link>
    </div>
  </div>
</template>

<script setup>
// 7.2.2：普通用户直接输入管理路由时进入本页，不白屏、不无限跳转。
</script>

<style scoped>
.forbidden-container {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  width: 100%;
  padding: 16px;
  box-sizing: border-box;
  background-color: var(--gray-100, #f5f5f5);
}

.forbidden-card {
  text-align: center;
  padding: 2.5rem 3rem;
  max-width: 480px;
  border-radius: 12px;
  background-color: var(--surface-raised, #fff);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
}

.forbidden-code {
  font-size: 4rem;
  font-weight: 700;
  color: var(--danger-600, #cf1322);
  line-height: 1;
  margin-bottom: 0.5rem;
}

.forbidden-title {
  font-size: 1.4rem;
  margin: 0 0 0.75rem 0;
  color: var(--text-primary, #333);
}

.forbidden-text {
  font-size: 0.95rem;
  color: var(--text-secondary, #666);
  margin: 0 0 1.5rem 0;
}
</style>
