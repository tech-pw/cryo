import { useAuth } from "../../lib/auth";
import { Alert } from "../ui/alert";

export const DevTokenProdBanner = () => {
  const { devTokenEnabledInProd } = useAuth();
  if (!devTokenEnabledInProd) return null;
  return (
    <Alert
      className="sticky top-12 z-20 mb-4"
      variant="warning"
      title="Anyone who can open this page can choose admin"
      description="There is no in-app switch. CRYO_ALLOW_DEV_TOKEN_IN_PROD is on. Do not turn it off until organization sign-in is working, or nobody can sign in."
    />
  );
};
