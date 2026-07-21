<template>
  <a
    v-if="objectUrl"
    :href="objectUrl"
    target="_blank"
    rel="noopener noreferrer"
    class="authenticated-image"
  >
    <img :src="objectUrl" :alt="alt" loading="lazy" />
    <span>{{ label }}</span>
  </a>
  <div v-else class="authenticated-image authenticated-image-state" :aria-label="alt">
    <a-spin v-if="loading" size="small" />
    <span v-else>{{ error || label }}</span>
  </div>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { useUserStore } from '@/stores/user'
import { fetchAuthenticatedBlob } from '@/utils/authenticated-image.mjs'

const props = defineProps({
  src: { type: String, required: true },
  alt: { type: String, default: '' },
  label: { type: String, default: '' },
})

const userStore = useUserStore()
const objectUrl = ref('')
const loading = ref(false)
const error = ref('')
let controller = null

const releaseObjectUrl = () => {
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
  objectUrl.value = ''
}

const loadImage = async () => {
  controller?.abort()
  const requestController = new AbortController()
  controller = requestController
  releaseObjectUrl()
  error.value = ''
  loading.value = true

  try {
    const blob = await fetchAuthenticatedBlob(props.src, userStore.token, fetch, requestController.signal)
    if (controller === requestController) objectUrl.value = URL.createObjectURL(blob)
  } catch (requestError) {
    if (requestError?.name !== 'AbortError') error.value = '图片加载失败'
  } finally {
    if (controller === requestController) loading.value = false
  }
}

watch(() => [props.src, userStore.token], loadImage, { immediate: true })

onBeforeUnmount(() => {
  controller?.abort()
  releaseObjectUrl()
})
</script>

<style lang="less" scoped>
.authenticated-image {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 6px;
  color: var(--gray-700);
  text-decoration: none;

  img,
  &.authenticated-image-state {
    width: 100%;
    aspect-ratio: 4 / 3;
    object-fit: contain;
    background: var(--gray-100);
    border: 1px solid var(--gray-200);
    border-radius: 6px;
  }

  span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.authenticated-image-state {
  align-items: center;
  justify-content: center;
  padding: 12px;

  span {
    white-space: normal;
  }
}
</style>
