import { type ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface SidebarCardProps {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** Sidebar panel — replaces antd Card with `td-sidebar-card` styling. */
export const SidebarCard = ({ title, children, className }: SidebarCardProps) => (
  <div className={cn("td-sidebar-card ant-card ant-card-small ant-card-bordered", className)}>
    {title ? (
      <div className="ant-card-head">
        <div className="ant-card-head-wrapper">
          <div className="ant-card-head-title">{title}</div>
        </div>
      </div>
    ) : null}
    <div className="ant-card-body">{children}</div>
  </div>
);
