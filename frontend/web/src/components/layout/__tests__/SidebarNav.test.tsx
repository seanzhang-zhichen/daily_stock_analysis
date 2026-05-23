import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SidebarNav } from '../SidebarNav';

const mockLogout = vi.fn().mockResolvedValue(undefined);

const completionBadgeState = { value: true };
const authState = {
  authEnabled: true,
  userMode: null as null | {
    userModeEnabled: boolean;
    loggedIn: boolean;
    user: { isAdmin?: boolean } | null;
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

describe('SidebarNav', () => {
  beforeEach(() => {
    completionBadgeState.value = true;
    authState.authEnabled = true;
    authState.userMode = null;
    mockLogout.mockClear();
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
    expect(screen.getByRole('link', { name: '我的' })).toBeInTheDocument();
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
    expect(screen.getByRole('link', { name: '我的' })).toBeInTheDocument();
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
