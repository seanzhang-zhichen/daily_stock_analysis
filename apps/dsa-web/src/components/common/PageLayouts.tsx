import type React from 'react';
import { cn } from '../../utils/cn';

type LayoutProps = {
  children: React.ReactNode;
  className?: string;
} & React.HTMLAttributes<HTMLElement>;

export const StandardPageLayout: React.FC<LayoutProps> = ({ children, className = '', ...props }) => {
  return <main className={cn('standard-page-layout layout-section-stack', className)} {...props}>{children}</main>;
};

export const WorkspacePageLayout: React.FC<LayoutProps> = ({ children, className = '', ...props }) => {
  return <main className={cn('workspace-page-layout layout-section-stack', className)} {...props}>{children}</main>;
};

export const ChatWorkspaceLayout: React.FC<LayoutProps> = ({ children, className = '', ...props }) => {
  return <main className={cn('chat-workspace-layout', className)} {...props}>{children}</main>;
};

export const AuthLayout: React.FC<LayoutProps> = ({ children, className = '', ...props }) => {
  return <main className={cn('auth-layout', className)} {...props}>{children}</main>;
};
