import { describe, expect, it } from 'vitest';
import { parseApiError } from '../error';

describe('parseApiError', () => {
  it('preserves structured API error codes for caller-specific recovery', () => {
    const parsed = parseApiError({
      response: {
        status: 403,
        data: {
          error: 'email_not_verified',
          message: '请先完成邮箱验证',
        },
      },
    });

    expect(parsed.code).toBe('email_not_verified');
    expect(parsed.message).toBe('请先完成邮箱验证');
  });
});
