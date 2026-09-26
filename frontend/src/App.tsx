import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Welcome from './components/Welcome'
import Approval from './pages/Approval'
import Architecture from './pages/Architecture'
import Audit from './pages/Audit'
import Copilot from './pages/Copilot'
import Dashboard from './pages/Dashboard'
import Deploy from './pages/Deploy'
import DeploymentDetail from './pages/DeploymentDetail'
import Deployments from './pages/Deployments'
import GpuInventory from './pages/GpuInventory'
import Infrastructure from './pages/Infrastructure'
import Integrations from './pages/Integrations'
import Login from './pages/Login'
import Observability from './pages/Observability'
import Recommendations from './pages/Recommendations'
import { AppProviders, useAuth } from './state'
import { Spinner } from './components/ui'

function Shell() {
  const { username, ready } = useAuth()

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Starting the control plane" />
      </div>
    )
  }
  if (!username) return <Login />

  return (
    <Layout>
      <Welcome />
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/copilot" element={<Copilot />} />
        <Route path="/deploy" element={<Deploy />} />
        <Route path="/recommendations" element={<Recommendations />} />
        <Route path="/recommendations/:id" element={<Recommendations />} />
        <Route path="/deployments" element={<Deployments />} />
        <Route path="/deployments/:id" element={<DeploymentDetail />} />
        <Route path="/approval/:id" element={<Approval />} />
        <Route path="/infrastructure" element={<Infrastructure />} />
        <Route path="/gpus" element={<GpuInventory />} />
        <Route path="/observability" element={<Observability />} />
        <Route path="/architecture" element={<Architecture />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="/settings/integrations" element={<Integrations />} />
        <Route path="*" element={<Dashboard />} />
      </Routes>
    </Layout>
  )
}

export default function App() {
  return (
    <AppProviders>
      <Shell />
    </AppProviders>
  )
}
