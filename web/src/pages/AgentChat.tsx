import { useState } from 'react'
import { Spin, Typography } from 'antd'

const { Text } = Typography

/**
 * 智能问答（问数智能体）
 *
 * 2026-10-15 融合改造：以 iframe 嵌入后端托管的 `/agent/`（秘书问答页），
 * 让问答功能留在 SPA 的导航框架内 —— 点进来后**侧边栏、顶部身份区不再消失**，
 * 消除「点一下像进了另一个系统」的割裂感。
 *
 * 为什么用 iframe 而不是把问答页重写成 React：
 *  - 问答页含 SSE 流式输出、工具调用轨迹、会话管理、语音上传等复杂逻辑，
 *    嵌入是零风险改动；
 *  - 重写收益只是「少一层 iframe」，却要承担引入 bug 的风险。
 *  等系统稳定运行一段时间后再评估是否重写。
 *
 * 认证：与父页面**同源**（都在 :8200），iframe 内可直接读到同一份
 * localStorage（`kb_token`），登录态天然共享，无需额外传参。
 */
export default function AgentChat() {
  const [loading, setLoading] = useState(true)

  return (
    <div style={{ position: 'relative', height: 'calc(100vh - 96px)' }}>
      {loading && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 12,
            background: '#fff',
            borderRadius: 8,
          }}
        >
          <Spin size="large" />
          <Text type="secondary">智能问答加载中…</Text>
        </div>
      )}
      <iframe
        src="/agent/"
        title="智能问答"
        onLoad={() => setLoading(false)}
        style={{
          width: '100%',
          height: '100%',
          border: 'none',
          borderRadius: 8,
          background: '#fff',
          // 加载完成前先透明，避免闪烁
          opacity: loading ? 0 : 1,
          transition: 'opacity .2s',
        }}
      />
    </div>
  )
}