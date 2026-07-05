import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Spin, Typography } from "antd";
import { useAuth } from "../lib/auth";

/** Handles OIDC authorization-code callback and stores the IdP access token. */
export const LoginCallback = () => {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { completeOidcSignIn, error } = useAuth();

  useEffect(() => {
    const code = params.get("code");
    if (!code) {
      navigate("/", { replace: true });
      return;
    }
    void completeOidcSignIn(code).then(() => navigate("/health", { replace: true }));
  }, [completeOidcSignIn, navigate, params]);

  return (
    <div className="login-screen">
      <Spin size="large" />
      {error ? (
        <Typography.Paragraph type="danger" style={{ marginTop: 16 }}>
          {error}
        </Typography.Paragraph>
      ) : (
        <Typography.Text type="secondary" style={{ marginTop: 16 }}>
          Completing sign-in…
        </Typography.Text>
      )}
    </div>
  );
};
