import { useAuth } from "../../lib/auth";
import { Alert } from "../ui/alert";

export const DevTokenProdBanner = () => {
  const { devTokenEnabledInProd } = useAuth();
  if (!devTokenEnabledInProd) return null;
  return (
    <Alert
      className="mb-4"
      variant="warning"
      title="Development sign-in is enabled on this production install"
      description="Anyone who can reach the API can sign in as any role, including admin, until development tokens are turned off."
    />
  );
};
