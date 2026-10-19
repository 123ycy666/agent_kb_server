import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { rbacApi } from '../api'
import type { AppUser, Role } from '../api'

const { Title, Text } = Typography

/**
 * 员工列表（统一用户表，只读）。
 *
 * 2026-10-14 起用户管理收敛为只读：
 * - 员工数据权威在人力资源系统（dlj_data.t_employee），新增 / 停用 / 删除都在那里操作
 * - 角色由 HR 的 administrator_flag 决定（1=管理员，0=普通用户），每次登录自动对齐
 * - 本页面只展示：员工基本信息 + 本地角色 + 是否已登录过（投影）
 *
 * ⚠️ 系统**只有两种角色**，不支持自定义：
 *    admin —— 管理员，可查看并处理全部文档与知识库（检索仍受公开/私有约束）
 *    user  —— 普通用户，可上传、检索公开文档与自己上传的文档
 */
export default function PermissionManagement() {
  const [users, setUsers] = useState<AppUser[]>([])
  const [roles, setRoles] = useState<Role[]>([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [u, r] = await Promise.allSettled([rbacApi.users(), rbacApi.roles()])
      if (u.status === 'fulfilled') setUsers(u.value)
      if (r.status === 'fulfilled') setRoles(r.value)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const columns: ColumnsType<AppUser> = [
    { title: '登录账号', dataIndex: 'username', width: 130 },
    {
      title: '姓名',
      dataIndex: 'actual_name',
      width: 90,
      render: (v?: string) => v || <Text type="secondary">—</Text>,
    },
    {
      title: '手机',
      dataIndex: 'phone',
      width: 130,
      render: (v?: string) => v || <Text type="secondary">—</Text>,
    },
    {
      title: '部门',
      dataIndex: 'department_id',
      width: 70,
      render: (v?: number) => (v ? `#${v}` : <Text type="secondary">—</Text>),
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      ellipsis: true,
      render: (v?: string) => v || <Text type="secondary">—</Text>,
    },
    {
      title: '角色',
      dataIndex: 'role_names',
      width: 110,
      render: (names: string[]) =>
        (names ?? []).length > 0 ? (
          (names ?? []).map((n) => (
            <Tag key={n} color={n === 'admin' ? 'red' : 'blue'}>
              {n === 'admin' ? '管理员' : '普通用户'}
            </Tag>
          ))
        ) : (
          <Text type="secondary">未登录</Text>
        ),
    },
    {
      title: 'HR 权限',
      dataIndex: 'administrator_flag',
      width: 90,
      render: (v: boolean) =>
        v ? <Tag color="red">系统管理员</Tag> : <Tag>普通员工</Tag>,
    },
    {
      title: '账号状态',
      width: 100,
      render: (_, row) => {
        if (row.deleted_flag) return <Tag color="default">已删除</Tag>
        if (row.disabled_flag) return <Tag color="orange">已停用</Tag>
        if (!row.projected) return <Tag color="default">未登录过</Tag>
        return <Tag color="green">正常</Tag>
      },
    },
  ]

  return (
    <div>
      <Space style={{ marginBottom: 16 }} align="baseline">
        <Title level={4} style={{ margin: 0 }}>
          员工列表
        </Title>
        <Text type="secondary">统一用户表：人力资源系统 t_employee（只读）</Text>
      </Space>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="用户体系统一到人力资源系统"
        description={
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li>员工账号、密码、部门、手机号以<b>人力资源系统（t_employee）</b>为准，本系统只读展示</li>
            <li>
              <b>新增 / 停用 / 删除员工</b>请联系人力资源系统管理员操作；下次登录自动同步到本系统
            </li>
            <li>
              角色由 HR 的「系统管理员」标记决定：标记为管理员的员工登录后即<b>管理员</b>，其他为<b>普通用户</b>
            </li>
            <li>
              <b>管理员</b>：可查看并处理全部文档与知识库；<b>但检索时与普通用户一样</b>
              ，只能搜到「公开的 + 自己上传的」
            </li>
            <li>改 / 删文档：<b>上传者本人或管理员</b></li>
          </ul>
        }
      />

      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
          <Text type="secondary">共 {users.length} 名员工（含已停用 / 已删除）</Text>
        </Space>

        <Table
          rowKey="username"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={users}
          pagination={{ pageSize: 20, showSizeChanger: false }}
        />
      </Card>

      {/* 角色字典（只读，便于理解权限模型） */}
      <Card title="角色与权限点（只读）" size="small" style={{ marginTop: 16 }}>
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={roles}
          pagination={false}
          columns={[
            { title: '角色', dataIndex: 'name', width: 100 },
            { title: '说明', dataIndex: 'description' },
            {
              title: '权限点数',
              dataIndex: 'permission_codes',
              width: 90,
              render: (codes?: string[]) => codes?.length ?? 0,
            },
            {
              title: '当前人数',
              dataIndex: 'user_count',
              width: 90,
              render: (n: number) => n ?? 0,
            },
          ]}
        />
      </Card>
    </div>
  )
}
