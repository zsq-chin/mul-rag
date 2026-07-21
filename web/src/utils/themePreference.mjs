export function resolveInitialTheme(savedTheme, prefersDark = false) {
  if (savedTheme === 'light' || savedTheme === 'dark') {
    return savedTheme
  }

  return prefersDark ? 'dark' : 'light'
}
