import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";

import { AuthProvider } from "./features/auth/AuthContext";
import { AppLayout } from "./layout/AppLayout";
import { ProtectedRoute, PublicOnlyRoute } from "./routes/RouteGuards";
import "./rag.css";

const KnowledgeBasesPage = lazy(() => import("./pages/KnowledgeBasesPage").then((module) => ({ default: module.KnowledgeBasesPage })));
const RagEvaluationPage = lazy(() => import("./pages/RagEvaluationPage").then((module) => ({ default: module.RagEvaluationPage })));

const AuthPage = lazy(() => import("./pages/AuthPage").then((module) => ({ default: module.AuthPage })));
const EvaluationPage = lazy(() =>
  import("./pages/EvaluationPage").then((module) => ({ default: module.EvaluationPage }))
);
const HistoryPage = lazy(() =>
  import("./pages/HistoryPage").then((module) => ({ default: module.HistoryPage }))
);
const AdminUsersPage = lazy(() =>
  import("./pages/AdminUsersPage").then((module) => ({ default: module.AdminUsersPage }))
);
const FeedbackStatsPage = lazy(() =>
  import("./pages/FeedbackStatsPage").then((module) => ({ default: module.FeedbackStatsPage }))
);
const ModelConfigsPage = lazy(() =>
  import("./pages/ModelConfigsPage").then((module) => ({ default: module.ModelConfigsPage }))
);
const ScoringRulesPage = lazy(() =>
  import("./pages/ScoringRulesPage").then((module) => ({ default: module.ScoringRulesPage }))
);

function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#16616a",
          colorSuccess: "#7fa36a",
          colorWarning: "#e5b85d",
          colorError: "#bc442b",
          borderRadius: 10,
          fontFamily: "Avenir Next, Helvetica Neue, Helvetica, Arial, sans-serif"
        }
      }}
    >
      <AuthProvider>
        <Suspense fallback={<main className="route-loading">正在加载页面...</main>}>
          <Routes>
            <Route
              path="/login"
              element={
                <PublicOnlyRoute>
                  <AuthPage mode="login" />
                </PublicOnlyRoute>
              }
            />
            <Route
              path="/register"
              element={
                <PublicOnlyRoute>
                  <AuthPage mode="register" />
                </PublicOnlyRoute>
              }
            />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <AppLayout />
                </ProtectedRoute>
              }
            >
              <Route index element={<EvaluationPage />} />
              <Route path="knowledge-bases" element={<KnowledgeBasesPage />} />
              <Route path="rag" element={<RagEvaluationPage />} />
              <Route path="models" element={<ModelConfigsPage />} />
              <Route path="embedding-config" element={<Navigate to="/models?tab=embedding" replace />} />
              <Route path="users" element={<AdminUsersPage />} />
              <Route path="scoring-rules" element={<ScoringRulesPage />} />
              <Route path="history" element={<HistoryPage key="chat-history" taskType="chat" />} />
              <Route path="rag/history" element={<HistoryPage key="rag-history" taskType="rag" />} />
              <Route path="feedback" element={<FeedbackStatsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </AuthProvider>
    </ConfigProvider>
  );
}

export default App;
