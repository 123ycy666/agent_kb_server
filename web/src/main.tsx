import React from 'react'
import ReactDOM from 'react-dom/client'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import App from './App'
import { AuthProvider } from './store/auth'
import './styles.css'

dayjs.locale('zh-cn')

const FONT =
  "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', " +
  "'Microsoft YaHei', 'Helvetica Neue', Arial, sans-serif"

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#1677ff',
          colorBgLayout: '#f5f7fa',
          colorBgContainer: '#ffffff',
          borderRadius: 6,
          borderRadiusLG: 8,
          borderRadiusSM: 4,
          fontSize: 13,
          fontFamily: FONT,
          colorTextHeading: 'rgba(0, 0, 0, 0.88)',
        },
        components: {
          Layout: { siderBg: '#001529', bodyBg: '#f5f7fa' },
          Menu: {
            darkItemBg: '#001529',
            darkSubMenuItemBg: '#000c17',
            darkItemSelectedBg: '#1677ff',
            itemHeight: 40,
          },
          Table: {
            headerBg: '#fafafa',
            headerColor: 'rgba(0, 0, 0, 0.88)',
            cellPaddingBlock: 10,
            cellPaddingInline: 12,
            rowHoverBg: '#f5f7fa',
          },
          Card: { paddingLG: 16 },
          Tabs: { horizontalItemPadding: '8px 0', titleFontSize: 13 },
          Form: { labelColor: 'rgba(0, 0, 0, 0.65)', verticalLabelPadding: '0 0 4px' },
        },
      }}
    >
      <AntApp>
        <AuthProvider>
          <App />
        </AuthProvider>
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>
)
