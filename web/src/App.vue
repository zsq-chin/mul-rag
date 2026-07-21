<script setup>
import { useThemeStore } from '@/stores/theme'

const themeStore = useThemeStore()
themeStore.applyTheme()
</script>
<template>
  <a-config-provider :theme="themeStore.antTheme">
    <router-view />
  </a-config-provider>
</template>
