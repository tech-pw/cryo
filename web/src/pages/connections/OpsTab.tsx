import { Settings } from "lucide-react";
import { OpsPanel } from "../../components/OpsPanel";

export const OpsTab = () => <OpsPanel />;

export const OpsTabLabel = () => (
  <span className="inline-flex items-center gap-1.5">
    <Settings className="size-3.5" aria-hidden="true" />
    Ops
  </span>
);
