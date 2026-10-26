import { AlertTriangle } from "lucide-react";
import { useAuth } from "../../lib/auth";

export const DevTokenProdBanner = () => {
  const { devTokenEnabledInProd } = useAuth();
  if (!devTokenEnabledInProd) return null;
  return (
    <div role="alert" className="cryo-dev-banner">
      <AlertTriangle aria-hidden="true" />
      <p>
        <span className="cryo-dev-banner__title">Anyone who can open this page can choose admin.</span>{" "}
        <span className="cryo-dev-banner__detail">
          <code>CRYO_ALLOW_DEV_TOKEN_IN_PROD</code> is on and there is no in-app switch. Keep it on until an
          admin can sign in with Google or your organization identity provider, or nobody can sign in.
        </span>
      </p>
    </div>
  );
};
