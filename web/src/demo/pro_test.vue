<template>
    <!-- 接受子组件向父组件传  在ChildProTest中写 ref 对应 defineExpose -->
    <!-- 接受子组件向父组件传  在ChildProTest中写 @test 对应 defineEmits -->
    <!-- 父组件发送子组件内容  在ChildProTest中写 :childInfo 对应 defineProps -->
    <ChildProTest @test="handtest" ref="child" :childInfo="childInfo"/>
    <button @click="childRef">Change Child Name</button>
</template>
<script setup>
import { ref, reactive} from 'vue'
import ChildProTest from './child_pro_test.vue'

const childInfo = reactive({
  name: 'John Doe',
  age: 18,
  school: 'XYZ High School'
})

const handtest = (value) => {
  console.log('Received from child:', value)
}

const child = ref()

const childRef = () => {
  console.log('childRef_value ', child.value)
  console.log('childRef_name: ', child.value.name.getAttribute('asca'))
  console.log('childRef_school: ', child.value.school)
}
</script>