<template>
  <div class="exam-container" v-if="exam">

    <!-- 主内容区域 (60%宽度) -->
    <div class="main-content">
      <!-- 考试标题 -->
      <h1 class="exam-title">{{ exam?.title }}</h1>

      <!-- 考生信息 + 倒计时 -->
      <div class="exam-header">
        <div class="exam-meta">
          <div class="meta-item">考生：{{ userStore.username }}</div>
          <div class="meta-item">总分：{{ exam?.questions?.length * 10 || 0 }} 分</div>
          <div class="meta-item">题数：{{ exam?.questions?.length || 0 }}</div>
          <div class="meta-item">考试时间：{{ exam?.duration || 0 }} 分钟</div>
        </div>
        <div class="timer-container">
          <span class="timer-label">剩余时间：</span>
          <a-statistic-countdown
            :value="deadline"
            format="HH:mm:ss"
            @finish="handleTimeUp"
            class="countdown-timer"
          />
        </div>
      </div>

      <!-- 考试规则 -->
      <div class="exam-rules">
        <h3>考试须知</h3>
        <ul>
          <li>请独立完成试题，不得作弊。</li>
          <li>考试时间为 {{ exam?.duration || 0 }} 分钟，超时系统自动提交。</li>
          <li>单选题、判断题、多选题答对得 10 分，多选题必须全对才得分。</li>
          <li>简答题需人工批阅，本系统暂不自动评分。</li>
        </ul>
      </div>

      <!-- 题目区 -->
      <div class="question-section" v-if="hasQuestions">
        <div
          v-for="(question, index) in allQuestions"
          :key="question.id"
          class="question-item"
        >
          <div class="question-header">
            <span class="question-number">第 {{ getQuestionNumber(index) }} 题</span>
            <span class="question-type">（{{ question.type }}）</span>
          </div>
          <div class="question-text">{{ question.question }}</div>

          <div class="answer-area">
            <!-- 单选题 -->
            <a-radio-group
              v-if="question.type === '单选题'"
              v-model:value="answers[index]"
              class="option-group"
            >
              <a-radio
                v-for="(option, key) in question.options"
                :value="key"
                :key="key"
                class="option-item"
              >
                <span class="option-key">{{ key }}.</span>
                <span class="option-text">{{ option }}</span>
              </a-radio>
            </a-radio-group>

            <!-- 多选题 -->
            <a-checkbox-group
              v-else-if="question.type === '多选题'"
              v-model:value="answers[index]"
              class="option-group"
            >
              <a-checkbox
                v-for="(option, key) in question.options"
                :value="key"
                :key="key"
                class="option-item"
              >
                <span class="option-key">{{ key }}.</span>
                <span class="option-text">{{ option }}</span>
              </a-checkbox>
            </a-checkbox-group>

            <!-- 判断题 -->
            <a-radio-group
              v-else-if="question.type === '判断题'"
              v-model:value="answers[index]"
              class="option-group"
            >
              <a-radio value="对" class="option-item">对</a-radio>
              <a-radio value="错" class="option-item">错</a-radio>
            </a-radio-group>

            <!-- 简答题 -->
            <div v-else class="short-answer">
              <a-textarea
                v-model:value="answers[index]"
                placeholder="请输入答案"
                :auto-size="{ minRows: 3, maxRows: 6 }"
                class="answer-textarea"
              />
            </div>
          </div>
        </div>
      </div>


    </div>

        <!-- 答题卡区域 (40%宽度) -->
    <div class="answer-card">
      <div class="card-header">答题卡</div>
      <div class="question-list">
        <div 
          v-for="(question, index) in allQuestions" 
          :key="index"
          class="question-status"
          :class="{ answered: answers[index] }"
          @click="scrollToQuestion(index)"
        >
          {{ index + 1 }}
        </div>
      </div>
      <a-button
        type="primary"
        size="large"
        @click="submitExam"
        class="submit-btn"
      >
        提交试卷
      </a-button>
            <!-- 提交通知 -->
      <div class="exam-footer">
        <div class="submit-notice">
          <a-alert
            message="提交后无法修改答案，请确认已完成所有题目"
            type="warning"
            show-icon
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { message } from 'ant-design-vue'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const props = defineProps({
  exam: {
    type: Object,
    required: true
  }
})
const emit = defineEmits(['submit'])

const answers = ref([])

const allQuestions = computed(() => props.exam.questions || [])
const hasQuestions = computed(() => allQuestions.value.length > 0)

const getQuestionNumber = (index) => index + 1

// 倒计时
const deadline = computed(() => {
  const now = new Date().getTime()
  return now + props.exam.duration * 60 * 1000
})

const handleTimeUp = () => {
  message.warning('考试时间已结束，系统将自动提交')
  submitExam()
}

const isSubmitting = ref(false)

const scrollToQuestion = (index) => {
  const questionEl = document.querySelectorAll('.question-item')[index]
  if (questionEl) {
    questionEl.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

const submitExam = () => {
  if (isSubmitting.value) return
  isSubmitting.value = true

  const score = calculateScore()

  emit('submit', {
    examId: props.exam.id,
    answers: answers.value,
    score
  })

  setTimeout(() => {
    isSubmitting.value = false
  }, 1000)
}

const calculateScore = () => {
  let score = 0
  props.exam.questions.forEach((question, index) => {
    const userAnswer = answers.value[index]
    if (!userAnswer) return

    if (question.type === '多选题') {
      const correct = question.answer.sort().join(',')
      const ua = Array.isArray(userAnswer) ? userAnswer.sort().join(',') : ''
      if (correct === ua) score += 10
    } else if (question.type === '单选题' || question.type === '判断题') {
      if (String(question.answer) === String(userAnswer)) score += 10
    } else if (question.type === '简答题') {
      // 简答题需要人工批改，这里不给分
      score += 0
    }
  })
  return score
}
</script>

<style scoped>
/* 整体布局 */
.exam-container {
  display: flex;
  margin: 0 auto;
  padding: 40px;
  gap: 100px;
  background: #fff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

/* 答题卡区域 (40%) */
.answer-card {
  position: sticky;
  width:30%;
  top: 20px;
  height: fit-content;
  background: #fff;
  padding: 20px;
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.1);
}

/* 主内容区域 (60%) */
.main-content {
  width:70%;
  overflow: auto;
  flex: 1;
}

.card-header {
  font-size: 18px;
  font-weight: bold;
  margin-bottom: 20px;
  text-align: center;
  color: #1890ff;
}

.question-list {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 20px;
}

.question-status {
  width: 36px;
  height: 36px;
  line-height: 36px;
  text-align: center;
  border-radius: 6px;
  background: #f5f5f5;
  cursor: pointer;
  transition: all 0.3s;
  font-size: 14px;
}

.question-status:hover {
  background: #e6f7ff;
  color: #1890ff;
}

.question-status.answered {
  background: #1890ff;
  color: #fff;
}

/* 标题 */
.exam-title {
  text-align: center;
  font-size: 28px;
  font-weight: bold;
  margin-bottom: 20px;
  color: #333;
}

/* 考生信息 */
.exam-header {
  margin-bottom: 20px;
}

.exam-meta {
  display: flex;
  justify-content: space-between;
  background: #fafafa;
  padding: 12px 20px;
  border: 1px solid #eee;
  border-radius: 6px;
  margin-bottom: 15px;
  font-size: 14px;
  color: #555;
}

.timer-container {
  text-align: right;
  margin-top: 10px;
}

.timer-label {
  font-size: 14px;
  margin-right: 5px;
  color: #666;
}

.countdown-timer {
  font-size: 22px;
  font-weight: bold;
  color: #d4380d;
}

/* 考试规则 */
.exam-rules {
  background: #f8f9fa;
  padding: 15px 20px;
  border-left: 4px solid #1890ff;
  border-radius: 4px;
  margin-bottom: 30px;
}
.exam-rules h3 {
  margin-bottom: 10px;
  font-size: 16px;
  color: #333;
}
.exam-rules ul {
  margin: 0;
  padding-left: 20px;
  color: #666;
  font-size: 14px;
}

/* 题目区 */
.question-item {
  border: 1px solid #eee;
  padding: 20px;
  margin-bottom: 25px;
  border-radius: 6px;
  background: #fcfcfc;
}
.question-header {
  font-size: 16px;
  margin-bottom: 10px;
  font-weight: bold;
}
.question-number {
  color: #1890ff;
  margin-right: 8px;
}
.question-type {
  color: #888;
}
.question-text {
  margin-bottom: 15px;
  line-height: 1.6;
  font-size: 15px;
}

/* 答案区域 */
.answer-area {
  padding-left: 10px;
}
.option-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.option-item {
  padding: 6px 10px;
  border-radius: 4px;
  transition: background 0.2s;
}
.option-item:hover {
  background: #f0f5ff;
}
.option-key {
  font-weight: bold;
  color: #1890ff;
  margin-right: 6px;
}
.answer-textarea {
  border-radius: 6px;
  border: 1px solid #d9d9d9;
  width: 100%;
}

/* 底部提交 */
.exam-footer {
  position: sticky;
  bottom: 0;
  background: #fff;
  padding: 20px;
  border-top: 1px solid #eee;
  text-align: center;
}
.submit-notice {
  margin-bottom: 15px;
}
.submit-btn {
  width: 100%;
  height: 45px;
  font-size: 16px;
  font-weight: 500;
  margin-top: 10px;
}
</style>
