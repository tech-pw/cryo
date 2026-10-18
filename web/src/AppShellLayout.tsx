import type { ReactNode } from "react";

import { FirstRunWizard } from "./components/onboarding/FirstRunWizard";
import { NoSourcesBanner } from "./components/NoSourcesBanner";
import { AppShell } from "./components/shell";

export interface AppShellLayoutProps {
  children: ReactNode;
}

export const AppShellLayout = ({ children }: AppShellLayoutProps) => (
  <AppShell>
    <NoSourcesBanner />
    <FirstRunWizard />
    {children}
  </AppShell>
);
