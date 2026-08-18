import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SidebarNav } from '../SidebarNav';

const preloadRouteModuleMock = vi.hoisted(() => vi.fn());
const mockLogout = vi.fn().mockResolvedValue(undefined);

const completionBadgeState = { value: true };
const authState = {
  authEnabled: true,
  loggedIn: true,
  userMode: null as null | {
    userModeEnabled: boolean;
    loggedIn: boolean;
    user: { isAdmin?: boolean; displayName?: string; email?: string; avatarUrl?: string } | null;
  },
  logout: mockLogout,
};

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => authState,
}));

vi.mock('../../../stores/agentChatStore', () => ({
  useAgentChatStore: (selector: (state: { completionBadge: boolean }) => unknown) =>
    selector({ completionBadge: completionBadgeState.value }),
}));

vi.mock('../../../utils/routePreload', () => ({
  preloadRouteModule: preloadRouteModuleMock,
}));

describe('SidebarNav', () => {
  beforeEach(() => {
    completionBadgeState.value = true;
    authState.authEnabled = true;
    authState.loggedIn = true;
    authState.userMode = null;
    mockLogout.mockClear();
    preloadRouteModuleMock.mockClear();
  });

  it('shows the shared completion badge only when chat completion is pending', () => {
    completionBadgeState.value = true;

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByTestId('chat-completion-badge')).toBeInTheDocument();
    expect(screen.getByLabelText('问股有新消息')).toBeInTheDocument();

    completionBadgeState.value = false;
    rerender(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByTestId('chat-completion-badge')).not.toBeInTheDocument();
  });

  it('does not render a sidebar theme toggle when the sidebar is collapsed', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav collapsed />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('button', { name: '切换主题' })).not.toBeInTheDocument();
  });

  it('routes help to the in-app help page', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '帮助' })).toHaveAttribute('href', '/help');
  });

  it('shows the portfolio entry in the main navigation', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '持仓' })).toHaveAttribute('href', '/portfolio');
  });

  it('preloads route modules when links are previewed', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const chatLink = container.querySelector<HTMLAnchorElement>('a[href="/chat"]');
    const helpLink = container.querySelector<HTMLAnchorElement>('a[href="/help"]');
    expect(chatLink).not.toBeNull();
    expect(helpLink).not.toBeNull();

    fireEvent.mouseEnter(chatLink!);
    fireEvent.focus(helpLink!);

    expect(preloadRouteModuleMock).toHaveBeenCalledWith('/chat');
    expect(preloadRouteModuleMock).toHaveBeenCalledWith('/help');
  });

  it('hides system settings for regular To C users', () => {
    authState.userMode = {
      userModeEnabled: true,
      loggedIn: true,
      user: { isAdmin: false },
    };

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: '设置' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '个人中心' })).toHaveAttribute('href', '/account');
    expect(screen.queryByRole('link', { name: '我的' })).not.toBeInTheDocument();
  });

  it('keeps system settings visible for To C admins', () => {
    authState.userMode = {
      userModeEnabled: true,
      loggedIn: true,
      user: { isAdmin: true },
    };

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '设置' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '个人中心' })).toHaveAttribute('href', '/account');
    expect(screen.queryByRole('link', { name: '我的' })).not.toBeInTheDocument();
  });

  it('limits navigation to public pages when access is locked', () => {
    authState.userMode = {
      userModeEnabled: true,
      loggedIn: false,
      user: null,
    };

    render(
      <MemoryRouter initialEntries={['/research-reports']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '研报' })).toHaveAttribute('href', '/research-reports');
    expect(screen.getByRole('link', { name: '公告' })).toHaveAttribute('href', '/notices');
    expect(screen.queryByRole('link', { name: '首页' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '问股' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '设置' })).not.toBeInTheDocument();
  });

  it('opens the logout confirmation and confirms logout', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '退出' }));

    expect(await screen.findByRole('heading', { name: '退出登录' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认退出' }));
    expect(mockLogout).toHaveBeenCalled();
  });
});
