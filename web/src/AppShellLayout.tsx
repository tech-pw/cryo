import type { ReactNode } from "react";

import { DisplayTimeZoneSync } from "./components/DisplayTimeZoneSync";
import { FirstRunWizard } from "./components/onboarding/FirstRunWizard";
import { NoSourcesBanner } from "./components/NoSourcesBanner";
import { AppShell } from "./components/shell";

export interface AppShellLayoutProps {
  children: ReactNode;
}

export const AppShellLayout = ({ children }: AppShellLayoutProps) => (
  <AppShell>
    <DisplayTimeZoneSync />
    <NoSourcesBanner />
    <FirstRunWizard />
    {children}
  </AppShell>
);
