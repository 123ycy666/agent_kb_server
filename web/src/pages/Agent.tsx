import Page from '../components/ui/Page'
import AgentConfigPanel from '../components/agent/AgentConfigPanel'

export default function Agent() {
  return (
    <Page title="智能体">
      <div style={{ background: '#fff', borderRadius: 8, padding: 16 }}>
        <AgentConfigPanel />
      </div>
    </Page>
  )
}
