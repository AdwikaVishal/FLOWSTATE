import { Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { ChaosLabPage } from './pages/ChaosLabPage'
import { DecisionInboxPage } from './pages/DecisionInboxPage'
import { ExecutionInspectorPage } from './pages/ExecutionInspectorPage'
import { InvoiceSlicePage } from './pages/InvoiceSlicePage'
import { LearningStudioPage } from './pages/LearningStudioPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { OverviewPage } from './pages/OverviewPage'
import { RecoveryAuditPage } from './pages/RecoveryAuditPage'
import { RunDetailPage } from './pages/RunDetailPage'
import { SettingsPage } from './pages/SettingsPage'
import { WorkflowDetailPage } from './pages/WorkflowDetailPage'
import { WorkflowStudioPage } from './pages/WorkflowStudioPage'

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<OverviewPage />} />
        <Route path="slice" element={<InvoiceSlicePage />} />
        <Route path="workflows" element={<WorkflowStudioPage />} />
        <Route path="workflows/:workflowId" element={<WorkflowDetailPage />} />
        <Route path="learning" element={<LearningStudioPage />} />
        <Route path="runs" element={<ExecutionInspectorPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="chaos" element={<ChaosLabPage />} />
        <Route path="decisions" element={<DecisionInboxPage />} />
        <Route path="recovery" element={<RecoveryAuditPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}