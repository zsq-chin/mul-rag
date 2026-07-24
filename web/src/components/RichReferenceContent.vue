<template>
  <div class="rich-reference-content">
    <div v-if="sanitizedContent" class="rich-reference-scroll" v-html="sanitizedContent"></div>

    <div v-if="standaloneImages.length" class="rich-reference-images">
      <button
        v-for="image in standaloneImages"
        :key="image.url"
        type="button"
        class="rich-reference-image"
        :aria-label="`预览图片：${image.alt}`"
        @click="previewImage = image"
      >
        <AuthenticatedImage
          :src="image.url"
          :alt="image.alt"
          :label="image.label"
          :open-in-new-tab="false"
        />
      </button>
    </div>

    <a-modal
      :open="Boolean(previewImage)"
      :title="previewImage?.alt || '图片预览'"
      :footer="null"
      :width="860"
      :destroy-on-close="true"
      @cancel="previewImage = null"
    >
      <AuthenticatedImage
        v-if="previewImage"
        class="rich-reference-preview"
        :src="previewImage.url"
        :alt="previewImage.alt"
        :label="previewImage.label"
        :open-in-new-tab="false"
      />
    </a-modal>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

import AuthenticatedImage from '@/components/AuthenticatedImage.vue'
import { renderRichContent, stripInlineImages, stripMarkdownImageSyntax } from '@/utils/richContent.mjs'

const props = defineProps({
  content: { type: String, default: '' },
  images: { type: Array, default: () => [] },
})

const previewImage = ref(null)

const hasImages = computed(() => props.images.some(Boolean))

const sanitizedContent = computed(() => {
  const source = hasImages.value ? stripMarkdownImageSyntax(props.content) : props.content
  const html = renderRichContent(source, window)
  return hasImages.value ? stripInlineImages(html, window) : html
})

function normalizeImage(image) {
  const raw = typeof image === 'string' ? { url: image } : (image || {})
  const url = raw.url || raw.src || ''
  if (!url) return null

  return {
    url,
    alt: raw.alt || raw.caption || raw.name || '检索结果图片',
    label: raw.label || raw.caption || raw.name || raw.alt || '检索结果图片',
  }
}

const inlineImageUrls = computed(() => {
  const document = new DOMParser().parseFromString(sanitizedContent.value, 'text/html')
  return new Set(Array.from(document.images, image => image.getAttribute('src') || ''))
})

const standaloneImages = computed(() => {
  const seen = new Set()
  return props.images
    .map(normalizeImage)
    .filter(Boolean)
    .filter(image => {
      if (seen.has(image.url) || inlineImageUrls.value.has(image.url)) return false
      seen.add(image.url)
      return true
    })
})
</script>

<style lang="less" scoped>
.rich-reference-content {
  min-width: 0;
}

.rich-reference-scroll {
  max-width: 100%;
  overflow-x: auto;
  color: var(--text-primary);
  line-height: 1.65;

  :deep(table) {
    width: 100%;
    min-width: 480px;
    border-collapse: collapse;
  }

  :deep(th),
  :deep(td) {
    padding: 8px 10px;
    border: 1px solid var(--border);
    text-align: left;
    vertical-align: top;
  }

  :deep(th) {
    background: var(--hover);
    font-weight: 600;
  }

  :deep(img) {
    display: block;
    max-width: 100%;
    height: auto;
    margin: 10px 0;
    border: 1px solid var(--border);
    border-radius: 6px;
  }

  :deep([data-rich-caption]),
  :deep(.rich-image-caption) {
    display: inline-block;
    padding: 2px 6px;
    margin: 2px 0;
    color: var(--text-secondary);
    font-size: 0.9em;
    background: var(--hover);
    border-radius: 4px;
  }

  :deep(pre) {
    max-width: 100%;
    overflow-x: auto;
  }

  :deep(a) {
    overflow-wrap: anywhere;
  }

  :deep(p:last-child) {
    margin-bottom: 0;
  }
}

.rich-reference-images {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.rich-reference-image {
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: zoom-in;
}

.rich-reference-image:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 3px;
}

:deep(.rich-reference-preview img),
:deep(.rich-reference-preview.authenticated-image-state) {
  max-height: 70vh;
  aspect-ratio: auto;
}
</style>
