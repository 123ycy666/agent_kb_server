export type UiPrefs = { sider: 'dark' | 'light'; font: number }

const KEY = 'kb_ui_prefs'
const DEFAULTS: UiPrefs = { sider: 'dark', font: 13 }

export const uiPrefs = (): UiPrefs => {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) }
  } catch {
    /* 存坏了就用默认 */
  }
  return DEFAULTS
}

export const setUiPrefs = (p: UiPrefs) => {
  localStorage.setItem(KEY, JSON.stringify(p))
  window.dispatchEvent(new Event('kb-ui-prefs'))
}
