<template>
  <div class="exam-view-container">
    <ExamInterfaceComponent 
      :exam="currentExam"
      @submit="handleExamSubmit"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { examRecordApi } from '@/apis/auth_api'
import ExamInterfaceComponent from '@/components/ExamInterfaceComponent.vue'

const route = useRoute()
const currentExam = ref(null)

onMounted(async () => {
  const examId = route.params.id
  if (examId) {
    try {
      const res = await examRecordApi.getExamRecords()
      const item = res.find(el => el.content.id === examId)
      currentExam.value = item.content
    } catch (error) {
      console.error('加载考试失败:', error)
    }
  }
})

window.addEventListener('beforeunload', () => {
  if (window.opener) {
    window.opener.postMessage({ type: 'examClosed' }, '*');
  }
});


const handleExamSubmit = (result) => {
  window.opener.postMessage({
    type: 'examSubmitted',
    data: JSON.stringify(result)  // 序列化为JSON字符串
  }, '*')
  window.close()
}
</script>

<style scoped>
.exam-view-container {
  width: 100%;
  height: 100vh;
  padding: 20px;
  background: #f8f9fa;
  /* overflow: auto; */
}
</style>
