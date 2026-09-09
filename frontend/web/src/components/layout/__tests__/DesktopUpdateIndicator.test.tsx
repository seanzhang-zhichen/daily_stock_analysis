import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DesktopUpdateIndicator } from '../DesktopUpdateIndicator';

const getUpdateState = vi.fn();
const checkForUpdates = vi.fn();
const openReleasePage = vi.fn();
const installDownloadedUpdate = vi.fn();

function renderIndicator() { return render(<MemoryRouter><DesktopUpdateIndicator /></MemoryRouter>); }

describe('DesktopUpdateIndicator', () => {
  beforeEach(() => {
    getUpdateState.mockResolvedValue({ status: 'idle', currentVersion: '3.31.0' });
    checkForUpdates.mockResolvedValue({ status: 'up-to-date', currentVersion: '3.31.0' });
    openReleasePage.mockResolvedValue(true); installDownloadedUpdate.mockResolvedValue(true);
    (window as Window & { dsaDesktop?: unknown }).dsaDesktop = { version: '3.31.0', getUpdateState, checkForUpdates, openReleasePage, installDownloadedUpdate, onUpdateStateChange: () => () => undefined };
  });
  afterEach(() => { vi.clearAllMocks(); delete (window as Window & { dsaDesktop?: unknown }).dsaDesktop; });
  it('stays hidden in normal browsers', () => { delete (window as Window & { dsaDesktop?: unknown }).dsaDesktop; renderIndicator(); expect(screen.queryByRole('button', { name: '桌面端更新' })).not.toBeInTheDocument(); });
  it('shows update state and opens a release link', async () => {
    getUpdateState.mockResolvedValue({ status: 'update-available', updateMode: 'manual', currentVersion: '3.30.0', latestVersion: '3.31.0', releaseUrl: 'https://example.test/releases' });
    renderIndicator(); fireEvent.click(await screen.findByRole('button', { name: '桌面端更新' }));
    expect(await screen.findByText('发现新版本')).toBeInTheDocument(); fireEvent.click(screen.getByRole('button', { name: '前往下载' }));
    await waitFor(() => expect(openReleasePage).toHaveBeenCalledWith('https://example.test/releases'));
  });
  it('checks only after an explicit action', async () => {
    renderIndicator(); fireEvent.click(await screen.findByRole('button', { name: '桌面端更新' }));
    expect(checkForUpdates).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', { name: '检查更新' }));
    await waitFor(() => expect(checkForUpdates).toHaveBeenCalledTimes(1));
  });
});
