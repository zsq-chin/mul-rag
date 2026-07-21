import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { theme as antTheme } from 'ant-design-vue'

import { themeTokens } from '@/assets/theme'
import { resolveInitialTheme } from '@/utils/themePreference.mjs'

const STORAGE_KEY = 'theme-mode'

function getInitialTheme() {
  if (typeof window === 'undefined') {
    return 'light'
  }

  return resolveInitialTheme(
    window.localStorage.getItem(STORAGE_KEY),
    window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false,
  )
}

export const useThemeStore = defineStore('theme', () => {
  const mode = ref(getInitialTheme())

  const antThemeConfig = computed(() => ({
    algorithm: mode.value === 'dark' ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
    token: {
      ...themeTokens,
      colorBgBase: mode.value === 'dark' ? '#17191c' : '#ffffff',
      colorTextBase: mode.value === 'dark' ? '#f0f2f3' : '#17191c',
      colorBorder: mode.value === 'dark' ? '#3b4248' : '#d9dee3',
    },
  }))

  function applyTheme() {
    if (typeof document === 'undefined') {
      return
    }

    document.documentElement.dataset.theme = mode.value
    document.documentElement.style.colorScheme = mode.value
  }

  function setMode(nextMode) {
    if (nextMode !== 'light' && nextMode !== 'dark') {
      return
    }

    mode.value = nextMode
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, nextMode)
    }
    applyTheme()
  }

  function toggle() {
    setMode(mode.value === 'dark' ? 'light' : 'dark')
  }

  return {
    mode,
    antTheme: antThemeConfig,
    applyTheme,
    setMode,
    toggle,
  }
})
