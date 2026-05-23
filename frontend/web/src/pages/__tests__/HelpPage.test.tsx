import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import HelpPage from '../HelpPage';

const authState = {
  userMode: null as null | {
    userModeEnabled: boolean;
    user: { isAdmin?: boolean } | null;
  },
};

vi.mock('../../hooks', () => ({
  useAuth: () => authState,
}));

describe('HelpPage', () => {
  beforeEach(() => {
    authState.userMode = null;
    document.title = 'DSA';
  });

  it('renders FAQ, support guidance, and risk disclosure', () => {
    render(
      <MemoryRouter>
        <HelpPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: '帮助中心', level: 1 })).toBeInTheDocument();
    expect(screen.getByText('常见问题')).toBeInTheDocument();
    expect(screen.getByText('反馈方式')).toBeInTheDocument();
    expect(screen.getByText('免责声明')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看公告' })).toHaveAttribute('href', '/notices');
    expect(screen.getByRole('link', { name: '反馈指引' })).toHaveAttribute('href', '#support');
    expect(screen.getByText(/联系服务维护者处理/)).toBeInTheDocument();
    expect(document.title).toBe('帮助中心 - DSA');
  });

  it('sends regular To C users to account settings instead of system settings', () => {
    authState.userMode = {
      userModeEnabled: true,
      user: { isAdmin: false },
    };

    render(
      <MemoryRouter>
        <HelpPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '打开账户设置' })).toHaveAttribute('href', '/account');
    expect(screen.queryByRole('link', { name: '打开系统设置' })).not.toBeInTheDocument();
  });

  it('keeps the system settings entry available for admins', () => {
    authState.userMode = {
      userModeEnabled: true,
      user: { isAdmin: true },
    };

    render(
      <MemoryRouter>
        <HelpPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '打开系统设置' })).toHaveAttribute('href', '/settings');
  });
});
