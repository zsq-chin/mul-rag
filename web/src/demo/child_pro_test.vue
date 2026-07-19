<template>
  <!-- ref写在元素上代表将这个元素视为一个响应式引用，双向绑定 -->
    <button @click="showinfo" ref="name" asca="asas">showinfo</button> 
    <button @click="showchildInfo">showchildInfo</button> 
</template>
<script setup>
import { ref, reactive} from 'vue'


const name = ref()
const school = ref('sage')
const emit = defineEmits(['test'])
defineExpose({
  name,
  school
})
const { childInfo } = defineProps(["childInfo"])
const showinfo = () => {
  emit('test', 'testtest')
  console.log('name', name)
  console.log('name', name.value)
  console.log('name', name.value.value)
  console.log('name', name.value.getAttribute('asca'))
}
const showchildInfo = ()=>{
  console.log('childInfo', childInfo.name)
  console.log('childInfo', childInfo.age)
  console.log('childInfo', childInfo.school)
}

</script>