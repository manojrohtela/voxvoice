import type { ComponentType } from "react";
import { Navigate, Route, Routes } from "react-router";

import { AppShell } from "../components/AppShell";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Loading } from "../components/Loading";
import { AgentEditorPage } from "../features/agents/AgentEditorPage";
import { AgentsPage } from "../features/agents/AgentsPage";
import { TemplatePreviewPage } from "../features/agents/TemplatePreviewPage";
import { AnalyticsPage } from "../features/analytics/AnalyticsPage";
import { ApiKeysPage } from "../features/developers/ApiKeysPage";
import { DeveloperDocsPage } from "../features/developers/DeveloperDocsPage";
import { SignInPage } from "../features/auth/SignInPage";
import { SignUpPage } from "../features/auth/SignUpPage";
import { CreateOrganizationPage } from "../features/onboarding/CreateOrganizationPage";
import { ConversationDetailPage } from "../features/conversations/ConversationDetailPage";
import { ConversationsPage } from "../features/conversations/ConversationsPage";
import { OverviewPage } from "../features/overview/OverviewPage";
import { PlaygroundPage } from "../features/playground/PlaygroundPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { UnavailablePage } from "../features/unavailable/UnavailablePage";
import { AuthProvider, useAuth } from "./auth";
import { hasCapability, MetaProvider, useConsoleMeta } from "./meta";
import { NAVIGATION, type NavItem } from "./navigation";

/** Implemented surfaces, by path. Everything else in NAVIGATION is "not yet available". */
const IMPLEMENTED: Record<string, ComponentType> = {
  "/agents": AgentsPage,
  "/analytics": AnalyticsPage,
  "/conversations": ConversationsPage,
  "/developers": ApiKeysPage,
  "/playground": PlaygroundPage,
  "/settings": SettingsPage,
};

/**
 * A navigation surface: its page when this build implements it and the server has not said it
 * lacks it. A server that declares its capabilities without this one (an older API) gets the
 * honest "not yet available" page instead of a page whose requests it cannot answer.
 */
function Surface({ item }: { item: NavItem }) {
  const meta = useConsoleMeta();
  const Page = IMPLEMENTED[item.path];
  const declined = meta.status === "ready" && !hasCapability(meta, item.capability);
  return Page && !declined ? <Page /> : <UnavailablePage item={item} />;
}

/** The console proper: only for a signed-in user who is working in an organization. */
function Protected() {
  const { state, refresh } = useAuth();
  if (state.status === "loading") return <Loading />;
  if (state.status === "error") {
    return <ErrorState title="Could not load your account" error={state.error} onRetry={() => void refresh()} />;
  }
  if (state.status === "signed_out") return <Navigate to="/login" replace />;
  if (state.me.organization === null) return <CreateOrganizationPage />;
  return <AppShell />;
}

export function App() {
  return (
    <MetaProvider>
      <AuthProvider>
        <Routes>
          <Route path="login" element={<SignInPage />} />
          <Route path="signup" element={<SignUpPage />} />
          <Route element={<Protected />}>
            <Route index element={<OverviewPage />} />
            <Route path="agents/new" element={<AgentEditorPage />} />
            <Route path="agents/templates/:templateId" element={<TemplatePreviewPage />} />
            <Route path="agents/:agentId" element={<AgentEditorPage />} />
            <Route path="conversations/:conversationId" element={<ConversationDetailPage />} />
            <Route path="developers/docs" element={<DeveloperDocsPage />} />
            {NAVIGATION.filter((item) => item.path !== "/").map((item) => {
              return <Route key={item.path} path={item.path.slice(1)} element={<Surface item={item} />} />;
            })}
            <Route
              path="*"
              element={
                <section className="page">
                  <EmptyState title="Page not found">
                    <p>Use the navigation to find what you need.</p>
                  </EmptyState>
                </section>
              }
            />
          </Route>
        </Routes>
      </AuthProvider>
    </MetaProvider>
  );
}
