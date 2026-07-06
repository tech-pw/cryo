import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Form, Input, Select, Space, Typography } from "antd";
import type { RoleToken } from "../api/types";
import { roleOptions, useAuth } from "../lib/auth";

interface LoginForm {
  subject: string;
  role: RoleToken;
}

export const Login = () => {
  const { signIn, startOidcSignIn, loading, error, authMode, devTokenEnabled } = useAuth();
  const [form] = Form.useForm<LoginForm>();

  if (authMode === "oidc") {
    return (
      <div className="login-screen">
        <Card className="login-card" title="Sign in to Cryo">
          <Space orientation="vertical" size={16} className="full-width">
            <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
              Production sign-in uses your organization identity provider. Dev token issuance is
              disabled.
            </Typography.Paragraph>
            {error ? <Alert type="error" showIcon title="Sign-in failed" description={error} /> : null}
            <Button type="primary" icon={<LockOutlined />} loading={loading} block onClick={startOidcSignIn}>
              Sign in with SSO
            </Button>
          </Space>
        </Card>
      </div>
    );
  }

  return (
    <div className="login-screen">
      <Card className="login-card" title="Sign in to Cryo">
        <Space orientation="vertical" size={16} className="full-width">
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            Cryo uses signed JWTs from the API. In dev, choose a role and sign in; production
            deployments disable the token endpoint and integrate your identity provider.
          </Typography.Paragraph>
          {!devTokenEnabled ? (
            <Alert
              type="warning"
              showIcon
              title="Dev token endpoint disabled"
              description="Configure OIDC (CRYO_OIDC_ISSUER_URL, CRYO_OIDC_CLIENT_ID) for production sign-in."
            />
          ) : null}
          {error ? <Alert type="error" showIcon title="Sign-in failed" description={error} /> : null}
          <Form<LoginForm>
            form={form}
            layout="vertical"
            initialValues={{ subject: "operator", role: "operator" }}
            onFinish={(values) => signIn(values.role, values.subject)}
          >
            <Form.Item
              name="subject"
              label="Username"
              rules={[{ required: true, message: "Enter a username" }]}
            >
              <Input prefix={<UserOutlined />} placeholder="your.name" autoComplete="username" />
            </Form.Item>
            <Form.Item name="role" label="Role" rules={[{ required: true }]}>
              <Select options={roleOptions} />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              icon={<LockOutlined />}
              loading={loading}
              block
              disabled={!devTokenEnabled}
            >
              Sign in
            </Button>
          </Form>
        </Space>
      </Card>
    </div>
  );
};
