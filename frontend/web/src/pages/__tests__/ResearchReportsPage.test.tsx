import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { researchReportsApi, type ResearchReport } from '../../api/researchReports';
import ResearchReportsPage from '../ResearchReportsPage';

const authState = {
  effectiveLoggedIn: false,
  userMode: {
    user: {
      creditBalance: 0,
      isResearchOperator: false,
    },
  },
  refreshStatus: vi.fn().mockResolvedValue(undefined),
};

vi.mock('../../hooks', () => ({
  useAuth: () => authState,
}));

vi.mock('../../api/researchReports', () => ({
  researchReportsApi: {
    list: vi.fn(),
    get: vi.fn(),
    comments: vi.fn(),
    purchase: vi.fn(),
    react: vi.fn(),
    comment: vi.fn(),
  },
}));

const reports: ResearchReport[] = [
  {
    id: 1,
    title: 'Alpha report',
    summary: 'Alpha summary',
    previewContent: 'Alpha preview',
    fullContent: 'Alpha full',
    category: '策略',
    tags: ['A股'],
    coverImageUrl: null,
    priceCredits: 0,
    isPublished: true,
    isUnlocked: true,
    likes: 1,
    dislikes: 0,
    commentsCount: 0,
    myReaction: null,
    publishedAt: '2026-06-01T08:00:00Z',
    createdAt: '2026-06-01T08:00:00Z',
    updatedAt: '2026-06-01T08:00:00Z',
  },
  {
    id: 2,
    title: 'Beta report',
    summary: 'Beta summary',
    previewContent: 'Beta preview',
    fullContent: 'Beta full',
    category: '行业',
    tags: ['港股'],
    coverImageUrl: null,
    priceCredits: 5,
    isPublished: true,
    isUnlocked: false,
    likes: 2,
    dislikes: 0,
    commentsCount: 0,
    myReaction: null,
    publishedAt: '2026-06-02T08:00:00Z',
    createdAt: '2026-06-02T08:00:00Z',
    updatedAt: '2026-06-02T08:00:00Z',
  },
];

describe('ResearchReportsPage', () => {
  beforeEach(() => {
    vi.mocked(researchReportsApi.list).mockResolvedValue({ reports, count: reports.length });
    vi.mocked(researchReportsApi.get).mockImplementation(async (id: number) => {
      const report = reports.find((item) => item.id === id);
      if (!report) throw new Error(`Unknown report ${id}`);
      return report;
    });
    vi.mocked(researchReportsApi.comments).mockResolvedValue({ comments: [], count: 0 });
    authState.effectiveLoggedIn = false;
    authState.userMode.user.creditBalance = 0;
    authState.userMode.user.isResearchOperator = false;
    authState.refreshStatus.mockClear();
  });

  it('uses the id query param without refetching the report list on selection changes', async () => {
    render(
      <MemoryRouter initialEntries={['/research-reports?id=2']}>
        <ResearchReportsPage />
      </MemoryRouter>,
    );

    await waitFor(() => expect(researchReportsApi.list).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(researchReportsApi.get).toHaveBeenCalledWith(2));

    fireEvent.click(await screen.findByRole('button', { name: /Alpha report/ }));

    await waitFor(() => expect(researchReportsApi.get).toHaveBeenCalledWith(1));
    expect(researchReportsApi.list).toHaveBeenCalledTimes(1);
  });
});
