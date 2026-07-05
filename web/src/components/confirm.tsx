import { Popconfirm, type PopconfirmProps } from "antd";
import type { ReactElement, ReactNode } from "react";

interface ConfirmActionProps extends Omit<PopconfirmProps, "children"> {
  children: ReactElement;
  title: ReactNode;
  description?: ReactNode;
}

/** Wraps destructive or fleet-wide actions with a mandatory confirmation step. */
export const ConfirmAction = ({
  title,
  description,
  children,
  okText = "Confirm",
  cancelText = "Cancel",
  okButtonProps,
  ...rest
}: ConfirmActionProps) => (
  <Popconfirm
    title={title}
    description={description}
    okText={okText}
    cancelText={cancelText}
    okButtonProps={{ danger: true, ...okButtonProps }}
    {...rest}
  >
    {children}
  </Popconfirm>
);
