import { Col, Row } from "antd";
import type { ReactNode } from "react";
import { PageHeader } from "../common";

interface OperationsConsolePageProps {
  title: string;
  subtitle?: string;
  listPane: ReactNode;
  detailPane?: ReactNode;
}

export const OperationsConsolePage = ({
  title,
  subtitle,
  listPane,
  detailPane
}: OperationsConsolePageProps) => (
  <div>
    <PageHeader title={title} subtitle={subtitle} />
    <Row gutter={[12, 12]}>
      <Col xs={24} xl={detailPane ? 11 : 24}>{listPane}</Col>
      {detailPane ? <Col xs={24} xl={13}>{detailPane}</Col> : null}
    </Row>
  </div>
);
