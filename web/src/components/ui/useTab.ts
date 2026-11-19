import { useSearchParams } from 'react-router-dom'

/** 页签写进 URL（`?tab=skills`），刷新不丢；非法值回落到第一个页签。 */
export function useTab(def: string, allowed: string[]) {
  const [sp, setSp] = useSearchParams()
  const raw = sp.get('tab') || ''
  const tab = allowed.includes(raw) ? raw : def
  const setTab = (next: string) =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev)
        n.set('tab', next)
        return n
      },
      { replace: true }
    )
  return [tab, setTab] as const
}
