import { useCallback, useRef, useState } from 'react'
import { agentHttp, getToken } from '../../api/client'
import { MIME, textOf, type FilePart, type Msg, type ToolPart } from './types'

let seq = 0
const newKey = (p: string) => p + ++seq

type SendOpts = {
  text: string
  fileIds: string[]
  sessionId?: string
  files?: FilePart[]
  onSession: (sid: string) => void
}

/** 把库里存的历史 item 还原成消息：user / assistant / tool_call / tool_result。 */
export function rowsToMsgs(rows: any[]): Msg[] {
  const out: Msg[] = []
  const lastAi = () => {
    for (let i = out.length - 1; i >= 0; i--) if (out[i].role === 'assistant') return out[i]
    const m: Msg = { key: newKey('h'), role: 'assistant', parts: [], status: 'complete' }
    out.push(m)
    return m
  }
  for (const r of rows) {
    const p = r.payload || {}
    const t = String(r.type || '')
    if (t === 'user') {
      out.push({ key: newKey('h'), role: 'user', text: textOf(p), parts: [], status: 'complete' })
    } else if (t === 'assistant') {
      const prev = out[out.length - 1]
      const body = textOf(p)
      if (prev && prev.role === 'assistant') {
        prev.parts.push({ kind: 'text', text: body })
      } else {
        out.push({ key: newKey('h'), role: 'assistant', parts: [{ kind: 'text', text: body }], status: 'complete' })
      }
    } else if (t === 'tool_call') {
      const m = lastAi()
      m.parts.push({
        kind: 'tool', toolCallId: String(p.call_id || p.id || newKey('c')),
        name: String(p.name || 'tool'), argsText: String(p.arguments || ''),
        status: 'complete', startedAt: 0,
      } as ToolPart)
    } else if (t === 'tool_result') {
      const m = lastAi()
      const body = typeof p.output === 'string' ? p.output : JSON.stringify(p.output ?? '')
      const hit = [...m.parts].reverse().find(
        (x) => x.kind === 'tool' && (x.toolCallId === String(p.call_id || '') || !(x as ToolPart).result),
      ) as ToolPart | undefined
      if (hit) {
        hit.result = body
        hit.isError = body.startsWith('工具执行失败')
        hit.status = hit.isError ? 'incomplete' : 'complete'
        if (hit.name === 'make_file') {
          try {
            const d = JSON.parse(body)
            if (d?.url) {
              m.parts.push({
                kind: 'file', fileId: String(d.file_id || ''), name: String(d.name || ''),
                size: Number(d.bytes || 0), mime: MIME[String(d.kind || '')] || '', url: String(d.url),
              })
            }
          } catch { /* 不是文件结果 */ }
        }
      }
    }
  }
  return out
}

export function useAgent() {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [running, setRunning] = useState(false)
  const [runId, setRunId] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  const hydrate = useCallback(async (sid: string) => {
    setMsgs([])
    setRunId('')
    if (!sid) return
    try {
      const r: any = await agentHttp.get(`/api/agent/sessions/${sid}/items`)
      setMsgs(rowsToMsgs(r.items || []))
    } catch { /* 读不出来就是空会话 */ }
  }, [])

  const patch = (key: string, fn: (m: Msg) => Msg) =>
    setMsgs((all) => all.map((m) => (m.key === key ? fn(m) : m)))

  const patchTool = (key: string, id: string, fn: (t: ToolPart) => ToolPart) =>
    patch(key, (m) => ({
      ...m,
      parts: m.parts.map((p) => (p.kind === 'tool' && p.toolCallId === id ? fn(p) : p)),
    }))

  const send = useCallback(async ({ text, fileIds, sessionId, files, onSession }: SendOpts) => {
    const akey = newKey('a')
    setMsgs((all) => [
      ...all,
      { key: newKey('u'), role: 'user', text, files: files || [], parts: [], status: 'complete' },
      { key: akey, role: 'assistant', parts: [], status: 'running' },
    ])
    setRunning(true)
    const ctl = new AbortController()
    abortRef.current = ctl
    try {
      const res = await fetch('/api/agent/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken() || ''}` },
        body: JSON.stringify({ text, session_id: sessionId || undefined, file_ids: fileIds }),
        signal: ctl.signal,
      })
      if (!res.ok || !res.body) throw new Error('服务没有返回流（' + res.status + '）')
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const chunks = buf.split('\n\n')
        buf = chunks.pop() || ''
        for (const chunk of chunks) {
          const line = chunk.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          let ev: any
          try { ev = JSON.parse(line.slice(6)) } catch { continue }
          if (ev.type === 'run') {
            setRunId(String(ev.run_id || ''))
            if (ev.session_id) onSession(String(ev.session_id))
          } else if (ev.type === 'text-delta') {
            patch(akey, (m) => {
              const parts = [...m.parts]
              const last = parts[parts.length - 1]
              if (last && last.kind === 'text') parts[parts.length - 1] = { kind: 'text', text: last.text + String(ev.text || '') }
              else parts.push({ kind: 'text', text: String(ev.text || '') })
              return { ...m, parts }
            })
          } else if (ev.type === 'tool-call-start') {
            patch(akey, (m) => ({
              ...m,
              parts: [...m.parts, {
                kind: 'tool', toolCallId: String(ev.tool_call_id || ''),
                name: String(ev.name || 'tool'), argsText: '',
                status: 'running', startedAt: Date.now(),
              } as ToolPart],
            }))
          } else if (ev.type === 'tool-call-args-delta') {
            patchTool(akey, String(ev.tool_call_id || ''), (t) => ({ ...t, argsText: t.argsText + String(ev.text || '') }))
          } else if (ev.type === 'tool-call-result') {
            patchTool(akey, String(ev.tool_call_id || ''), (t) => ({
              ...t,
              result: String(ev.result ?? ''),
              isError: Boolean(ev.is_error),
              status: ev.is_error ? 'incomplete' : 'complete',
              ms: Date.now() - (t.startedAt || Date.now()),
            }))
          } else if (ev.type === 'file') {
            patch(akey, (m) => ({
              ...m,
              parts: [...m.parts, {
                kind: 'file', fileId: String(ev.file_id || ''), name: String(ev.name || ''),
                size: Number(ev.size || 0), mime: MIME[String(ev.kind || '')] || '', url: String(ev.url || ''),
              } as FilePart],
            }))
          } else if (ev.type === 'error') {
            patch(akey, (m) => ({ ...m, status: 'error', error: String(ev.message || '出错了') }))
          } else if (ev.type === 'finish') {
            const st = String(ev.status || 'done')
            patch(akey, (m) => ({
              ...m,
              status: st === 'stopped' ? 'stopped' : st === 'failed' ? 'error' : 'complete',
              usage: Number((ev.usage || {}).tokens || 0),
              ms: Number(ev.ms || 0),
              error: ev.error ? String(ev.error) : m.error,
            }))
          }
        }
      }
    } catch (e: any) {
      patch(akey, (m) => ({ ...m, status: 'error', error: e?.message || String(e) }))
    } finally {
      setRunning(false)
      abortRef.current = null
    }
  }, [])

  const stop = useCallback(async () => {
    if (!runId) return
    try { await agentHttp.post('/api/agent/stop', { run_id: runId }) } catch { /* 已结束 */ }
  }, [runId])

  return { msgs, running, runId, send, stop, hydrate, setMsgs, setRunId }
}
