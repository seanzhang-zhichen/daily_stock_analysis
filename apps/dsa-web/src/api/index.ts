import axios from 'axios';
import { API_BASE_URL } from '../utils/constants';
import { attachParsedApiError } from './error';

export const QUOTA_EXCEEDED_EVENT = 'dsa:quota-exceeded';

export type QuotaExceededDetail = {
  kind: string;
  limit: number;
  used: number;
  remaining: number;
  planCode: string;
  planName: string;
  message: string;
};

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const path = window.location.pathname + window.location.search;
      if (!path.startsWith('/login')) {
        const redirect = encodeURIComponent(path);
        window.location.assign(`/login?redirect=${redirect}`);
      }
    }
    // 402 + error=quota_exceeded: 全局派发事件, 由 <QuotaExceededDialog /> 接管
    if (error.response?.status === 402 && error.response.data?.error === 'quota_exceeded') {
      const data = error.response.data as Partial<QuotaExceededDetail>;
      const detail: QuotaExceededDetail = {
        kind: typeof data.kind === 'string' ? data.kind : 'analysis',
        limit: typeof data.limit === 'number' ? data.limit : 0,
        used: typeof data.used === 'number' ? data.used : 0,
        remaining: typeof data.remaining === 'number' ? data.remaining : 0,
        planCode: typeof data.planCode === 'string' ? data.planCode : 'free',
        planName: typeof data.planName === 'string' ? data.planName : 'Free',
        message: typeof data.message === 'string' ? data.message : '今日额度已用完',
      };
      try {
        window.dispatchEvent(new CustomEvent<QuotaExceededDetail>(QUOTA_EXCEEDED_EVENT, { detail }));
      } catch {
        // ignore dispatch failures (e.g. SSR / non-DOM env)
      }
    }
    attachParsedApiError(error);
    return Promise.reject(error);
  }
);

export default apiClient;
