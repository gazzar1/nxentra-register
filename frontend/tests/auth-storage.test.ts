import { describe, it, expect } from 'vitest';
import {
  getAccessToken,
  getRefreshToken,
  storeTokens,
  removeTokens,
} from '@/lib/auth-storage';

describe('auth-storage', () => {
  it('returns null when no token stored', () => {
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it('stores and retrieves tokens', () => {
    storeTokens('access-123', 'refresh-456');
    expect(getAccessToken()).toBe('access-123');
    expect(getRefreshToken()).toBe('refresh-456');
  });

  it('removes tokens', () => {
    storeTokens('access-123', 'refresh-456');
    removeTokens();
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it('overwrites existing tokens', () => {
    storeTokens('old-access', 'old-refresh');
    storeTokens('new-access', 'new-refresh');
    expect(getAccessToken()).toBe('new-access');
    expect(getRefreshToken()).toBe('new-refresh');
  });
});
