<template>
  <div class="empty-container">
    <div class="empty-content">
      <h1 class="empty-title">待上线...</h1>
      <p class="empty-text">Sorry.liuxu has not been zuoed</p>
    </div>
  </div>
</template>

<script setup>
// 空视图组件，不需要任何逻辑
</script>

<style scoped>
.empty-container {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background-color: #f5f5f5;
}

.empty-content {
  text-align: center;
  padding: 2rem;
}

.empty-title {
  font-size: 3rem;
  margin-bottom: 1rem;
  color: #333;
}

.empty-text {
  font-size: 1.5rem;
  color: #666;
  font-style: italic;
}
</style>
