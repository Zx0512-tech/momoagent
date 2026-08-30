import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { LoadingSpinner } from "./components/feedback/LoadingSpinner";

// 对话式智能体是主入口。
const ChatPage = lazy(() => import("./pages/chat/ChatPage").then(m => ({ default: m.ChatPage })));

// 分析页全部保留，作为对话之外的深挖入口。
const DashboardPage = lazy(() => import("./pages/dashboard/DashboardPage"));
const LoadsPage = lazy(() => import("./pages/loads/LoadsPage"));
const CommandStreamPage = lazy(() => import("./pages/command-stream/CommandStreamPage"));
const SolverBatchPage = lazy(() => import("./pages/solver/SolverBatchPage"));
const ResultsPage = lazy(() => import("./pages/results/ResultsPage"));
const ExperimentDesignPage = lazy(() => import("./pages/experiment-design/ExperimentDesignPage"));
const DamperBasePage = lazy(() => import("./pages/damper-base/DamperBasePage"));
const SurrogatePage = lazy(() => import("./pages/surrogate/SurrogatePage"));
const OptimizationPage = lazy(() => import("./pages/optimization/OptimizationPage"));
const ArtifactBrowserPage = lazy(() => import("./pages/artifacts/ArtifactBrowserPage"));

function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Suspense fallback={<LoadingSpinner />}>
          <Routes>
            <Route path="/" element={<ChatPage />} />
            {/* 旧的智能体工作台入口指向对话页 */}
            <Route path="/agent" element={<Navigate to="/" replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/loads" element={<LoadsPage />} />
            <Route path="/command-stream" element={<CommandStreamPage />} />
            <Route path="/solver" element={<SolverBatchPage />} />
            <Route path="/results" element={<ResultsPage />} />
            <Route path="/experiment-design" element={<ExperimentDesignPage />} />
            <Route path="/damper-base" element={<DamperBasePage />} />
            <Route path="/surrogate" element={<SurrogatePage />} />
            <Route path="/optimization" element={<OptimizationPage />} />
            <Route path="/artifacts" element={<ArtifactBrowserPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </AppShell>
    </BrowserRouter>
  );
}

export default App;
