import type { ReactNode } from 'react'
import { Space, Typography } from 'antd'

const { Title } = Typography

/** 页面外壳：标题行只有「标题 + 右侧主操作」，全站不写用途说明。 */
export default function Page({
  title,
  extra,
  children,
  fill = false,
}: {
  title: string
  extra?: ReactNode
  children: ReactNode
  /** 内容区自己撑满高度（对话页用） */
  fill?: boolean
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: fill ? 'calc(100vh - 32px)' : undefined }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          marginBottom: 12,
          flex: '0 0 auto',
        }}
      >
        <Title level={4} style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
          {title}
        </Title>
        <Space size={8}>{extra}</Space>
      </div>
      <div style={{ flex: fill ? 1 : undefined, minHeight: 0 }}>{children}</div>
    </div>
  )
}
