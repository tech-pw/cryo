import type { ReactNode } from "react";

import { FirstRunWizard } from "./components/onboarding/FirstRunWizard";
import { NoSourcesBanner } from "./components/NoSourcesBanner";
import { AppShell } from "./components/shell";

export interface AppShellLayoutProps {
  children: ReactNode;
}

export const AppShellLayout = ({ children }: AppShellLayoutProps) => (
  <>
    <a
      href="#main-content"
      className="absolute -left-[9999px] top-2 z-[9999] rounded-md bg-primary px-4 py-2.5 font-semibold text-white no-underline shadow-md focus:left-2"
    >
      Skip to main content
    </a>
    <AppShell>
      <NoSourcesBanner />
      <FirstRunWizard />
      {children}
    </AppShell>
  </>
);
