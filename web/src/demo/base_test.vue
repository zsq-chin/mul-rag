<template>
    <div @click="Open = !Open" style="color: blue;">
        {{ Open ? '隐藏' : '显示' }}测试组件
        是否展示
    </div>
  <div v-if="Open">
    <h1>Test Component</h1>
    <p>This is a test component for demonstration purposes.</p>


    姓名<input v-model="userInfo.name" ref="input"/>
    薪水<input v-model="userInfo.salary" /><br/><br/>
        <div>
            <p>姓名: {{ name }}</p>
            <p>薪水: {{ salary }}</p>
        </div>
    <button @click="handleSubmit">submit</button><br/>
    <button @click="addSalary">增加薪水</button><br/>
    会的技能：<span class="skills-list" v-for="skill in userInfo.skills" :key="skill">{{ skill }}</span>
    <input v-model="newSkill" placeholder="添加技能"/>
    <button @click="addSkill">添加技能</button><br/>
  </div>
</template>
<script setup>
import { ref, reactive, toRefs} from 'vue'

const userInfo = reactive({
  name: 'liuxu',
  salary: 1000,
  skills: ['vue', 'react', 'nodejs']
})
const { name, salary, skills } = toRefs(userInfo)
const handleSubmit = () => {
  console.log('提交数据:', userInfo)
  // 在这里可以添加提交逻辑，比如发送到服务器
  alert(`提交成功: 姓名=${userInfo.name}, 薪水=${userInfo.salary}`)
}
const addSalary = () => {
  userInfo.salary += 100
  console.log('薪水增加后:', userInfo.salary)
}
const newSkill = ref('')
const Open = ref(true)
const addSkill = () => {
  if (newSkill.value.trim() !== '') {
    userInfo.skills.push(newSkill.value.trim())
    newSkill.value = '' // 清空输入框
  } else {
    alert('请输入有效的技能')
  }
}

const input = ref()
console.log('name', name)
console.log('name', name.value)
console.log('name', name.value.value)


</script>
<style scoped>
.skills-list {
  display: inline-block;
  margin-right: 10px;
  padding: 5px;
  background-color: #f0f0f0;
  border-radius: 4px;
  color: #333;
  gap: 10px;
    font-size: 14px;
}
.skills-list:hover {
  background-color: #e0e0e0;
  cursor: pointer;
}
</style>