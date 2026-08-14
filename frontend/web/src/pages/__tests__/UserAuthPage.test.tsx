import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import UserAuthPage from '../UserAuthPage';

const { navigate, useAuthMock, requestEmailVerificationMock, verifyEmailMock } = vi.hoisted(() => ({
  navigate: vi.fn(),
  useAuthMock: vi.fn(),
  requestEmailVerificationMock: vi.fn(),
  verifyEmailMock: vi.fn(),
}));

vi.mock('../../hooks', () => ({
  useAuth: () => useAuthMock(),
}));

vi.mock('../../api/account', () => ({
  accountApi: {
    requestEmailVerification: requestEmailVerificationMock,
    verifyEmail: verifyEmailMock,
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

const userMode = {
  userModeEnabled: true,
  registrationEnabled: true,
  requireEmailVerification: true,
  inviteRequired: false,
  loggedIn: false,
  user: null,
  plan: null,
  quota: null,
  credits: null,
  renewal: null,
  termsVersion: '2026-01-01',
};

describe('UserAuthPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    requestEmailVerificationMock.mockResolvedValue({
      ok: true,
      message: '如果账号存在且尚未验证，验证邮件已重新发送，请前往邮箱查收。',
    });
    verifyEmailMock.mockResolvedValue({ user: { email: 'new-user@example.com' } });
  });

  it('shows an email verification handoff after registration succeeds', async () => {
    const registerWithEmail = vi.fn().mockResolvedValue({ success: true, requiresVerification: true });
    useAuthMock.mockReturnValue({
      loginWithEmail: vi.fn(),
      registerWithEmail,
      userMode,
    });

    render(
      <MemoryRouter initialEntries={['/register']}>
        <UserAuthPage mode="register" />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText('邮箱地址'), { target: { value: ' new-user@example.com ' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password123' } });
    fireEvent.change(screen.getByLabelText('确认密码'), { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: '创建账号' }));

    await waitFor(() => {
      expect(registerWithEmail).toHaveBeenCalledWith({
        email: 'new-user@example.com',
        password: 'password123',
        passwordConfirm: 'password123',
        inviteCode: undefined,
        termsAgreed: true,
        termsVersion: '2026-01-01',
      });
    });

    expect(await screen.findByText('请查收验证邮件')).toBeInTheDocument();
    expect(screen.getByText('new-user@example.com')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('请输入 6 位验证码')).toBeInTheDocument();
    expect(screen.queryByLabelText('密码')).not.toBeInTheDocument();
  });

  it('continues email verification when login reports an unverified account', async () => {
    const loginWithEmail = vi.fn().mockResolvedValue({
      success: false,
      error: {
        title: '请求失败',
        message: '请先完成邮箱验证',
        rawMessage: '请先完成邮箱验证',
        status: 403,
        code: 'email_not_verified',
        category: 'http_error',
      },
    });
    useAuthMock.mockReturnValue({
      loginWithEmail,
      registerWithEmail: vi.fn(),
      userMode,
    });

    render(
      <MemoryRouter initialEntries={['/login']}>
        <UserAuthPage mode="login" />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText('邮箱地址'), { target: { value: ' pending@example.com ' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('button', { name: '立即登录' }));

    await waitFor(() => expect(loginWithEmail).toHaveBeenCalledWith('pending@example.com', 'password123'));
    expect(await screen.findByText('请查收验证邮件')).toBeInTheDocument();
    expect(screen.getByText('pending@example.com')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新发送验证邮件' }));
    await waitFor(() => expect(requestEmailVerificationMock).toHaveBeenCalledWith('pending@example.com'));

    fireEvent.change(screen.getByPlaceholderText('请输入 6 位验证码'), { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: '验证邮箱' }));

    await waitFor(() => expect(verifyEmailMock).toHaveBeenCalledWith('pending@example.com', '123456'));
    expect(await screen.findByText('邮箱验证成功，请登录。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '立即登录' })).toBeInTheDocument();
  });

  it('guides duplicate registrations to login and verification', async () => {
    const registerWithEmail = vi.fn().mockResolvedValue({
      success: false,
      error: {
        title: '请求失败',
        message: '该邮箱已注册',
        rawMessage: '该邮箱已注册',
        status: 409,
        code: 'email_already_registered',
        category: 'http_error',
      },
    });
    useAuthMock.mockReturnValue({
      loginWithEmail: vi.fn(),
      registerWithEmail,
      userMode,
    });

    render(
      <MemoryRouter initialEntries={['/register']}>
        <UserAuthPage mode="register" />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText('邮箱地址'), { target: { value: 'pending@example.com' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password123' } });
    fireEvent.change(screen.getByLabelText('确认密码'), { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: '创建账号' }));

    expect(await screen.findByText(/登录后可继续输入或重新发送验证码/)).toBeInTheDocument();
  });

  it('resends the verification email from the handoff page', async () => {
    const registerWithEmail = vi.fn().mockResolvedValue({ success: true, requiresVerification: true });
    useAuthMock.mockReturnValue({
      loginWithEmail: vi.fn(),
      registerWithEmail,
      userMode,
    });

    render(
      <MemoryRouter initialEntries={['/register']}>
        <UserAuthPage mode="register" />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText('邮箱地址'), { target: { value: 'new-user@example.com' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password123' } });
    fireEvent.change(screen.getByLabelText('确认密码'), { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: '创建账号' }));

    await screen.findByText('请查收验证邮件');
    fireEvent.click(screen.getByRole('button', { name: '重新发送验证邮件' }));

    await waitFor(() => expect(requestEmailVerificationMock).toHaveBeenCalledWith('new-user@example.com'));
    expect(await screen.findByText('已重新发送')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '60 秒后可重新发送' })).toBeDisabled();
  });
});
