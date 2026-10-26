import { AlertTriangle } from "lucide-react";
import { Link } from "react-router-dom";

import { shortSha, useApiVersion, versionsDiffer, webBuild } from "../../lib/buildInfo";

/** Sidebar footer: the running API version, linking to Settings > About (#263). */
export const VersionFooter = ({ onNavigate }: { onNavigate?: () => void }) => {
  const { data } = useApiVersion();
  if (!data) return null;
  const web = webBuild();
  const mismatch = versionsDiffer(web, data);
  const sha = shortSha(data.git_sha);
  const label = `Cryo ${data.version}${sha ? ` · ${sha}` : ""}`;
  return (
    <Link
      to="/settings/about"
      className="cryo-shell__version"
      onClick={onNavigate}
      title={mismatch ? `${label}. The web build is ${web.version}; reload or redeploy.` : label}
    >
      {mismatch ? <AlertTriangle size={12} aria-label="Web and API versions differ" /> : null}
      <span>{label}</span>
    </Link>
  );
};
