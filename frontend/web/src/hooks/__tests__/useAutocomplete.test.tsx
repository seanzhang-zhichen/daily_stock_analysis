/**
 * useAutocomplete hook tests.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAutocomplete } from '../useAutocomplete';
import type { StockIndexItem } from '../../types/stockIndex';

const stockSearchMock = vi.hoisted(() => vi.fn());

vi.mock('../../api/stocks', () => ({
  stocksApi: {
    search: (...args: unknown[]) => stockSearchMock(...args),
  },
}));

const mockIndex: StockIndexItem[] = [
  {
    canonicalCode: '600519.SH',
    displayCode: '600519',
    nameZh: '贵州茅台',
    pinyinFull: 'guizhoumaotai',
    pinyinAbbr: 'gzmt',
    aliases: ['茅台'],
    market: 'CN',
    assetType: 'stock',
    active: true,
    popularity: 100,
  },
];

describe('useAutocomplete', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    stockSearchMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps autocomplete usable after a transient search error', async () => {
    stockSearchMock.mockRejectedValueOnce(new Error('Search exploded'));

    const { result } = renderHook(() => useAutocomplete(mockIndex, { debounceMs: 10 }));

    act(() => {
      result.current.setQuery('茅台');
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });

    expect(result.current.runtimeFallback).toBe(false);
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.isOpen).toBe(false);
    expect(result.current.suggestions).toEqual([]);

    stockSearchMock.mockResolvedValueOnce([
      {
        canonicalCode: '600519.SH',
        displayCode: '600519',
        nameZh: '贵州茅台',
        market: 'CN',
        matchType: 'exact',
        matchField: 'alias',
        score: 97,
      },
    ]);

    act(() => {
      result.current.setQuery('茅台');
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });

    expect(result.current.error).toBeNull();
    expect(result.current.isOpen).toBe(true);
    expect(result.current.suggestions).toHaveLength(1);
  });

  it('keeps suggestions open without auto-highlighting the first result', async () => {
    stockSearchMock.mockResolvedValue([
      {
        canonicalCode: '600519.SH',
        displayCode: '600519',
        nameZh: '贵州茅台',
        market: 'CN',
        matchType: 'exact',
        matchField: 'code',
        score: 100,
      },
    ]);

    const { result } = renderHook(() => useAutocomplete(mockIndex, { debounceMs: 10 }));

    act(() => {
      result.current.setQuery('600519');
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });

    expect(result.current.isOpen).toBe(true);
    expect(result.current.suggestions).toHaveLength(1);
    expect(result.current.highlightedIndex).toBe(-1);
  });

  it('continues searching after aborting a previous request', async () => {
    stockSearchMock.mockImplementation((_query: string, _limit: number, signal?: AbortSignal) => (
      new Promise((resolve, reject) => {
        signal?.addEventListener('abort', () => {
          const error = new Error('canceled') as Error & { code: string };
          error.name = 'CanceledError';
          error.code = 'ERR_CANCELED';
          reject(error);
        });
        setTimeout(() => resolve([
          {
            canonicalCode: '600519.SH',
            displayCode: '600519',
            nameZh: '贵州茅台',
            market: 'CN',
            matchType: 'exact',
            matchField: 'alias',
            score: 97,
          },
        ]), 1);
      })
    ));

    const { result } = renderHook(() => useAutocomplete(mockIndex, { debounceMs: 10 }));

    act(() => {
      result.current.setQuery('茅台');
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });

    act(() => {
      result.current.setQuery('贵州茅台');
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(11);
    });

    expect(stockSearchMock).toHaveBeenCalledTimes(2);
    expect(result.current.runtimeFallback).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.isOpen).toBe(true);
    expect(result.current.suggestions).toHaveLength(1);
  });

  it('searches when the query has one character by default', async () => {
    stockSearchMock.mockResolvedValue([]);

    const { result } = renderHook(() => useAutocomplete(mockIndex, { debounceMs: 10 }));

    act(() => {
      result.current.setQuery('茅');
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });

    expect(stockSearchMock).toHaveBeenCalledWith('茅', 10, expect.any(AbortSignal));
  });
});
