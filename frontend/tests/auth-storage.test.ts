import { describe, it, expect } from 'vitest';
import {
  isAuthenticated,
  setAuthenticated,
  cleanupLegacyTokens,
  getAccessToken,
  getRefreshToken,
  storeTokens,
  removeTokens,
} from '@/lib/auth-storage';

describe('auth-storage', () => {
  it('returns false when not authenticated', () => {
    expect(isAuthenticated()).toBe(false);
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it('sets and checks authenticated flag', () => {
    setAuthenticated(true);
    expect(isAuthenticated()).toBe(true);
    // Legacy compat: getAccessToken returns a truthy placeholder
    expect(getAccessToken()).toBe('__cookie__');
    expect(getRefreshToken()).toBe('__cookie__');
  });

  it('clears authenticated flag', () => {
    setAuthenticated(true);
    setAuthenticated(false);
    expect(isAuthenticated()).toBe(false);
    expect(getAccessToken()).toBeNull();
  });

  it('storeTokens sets authenticated flag (backward compat)', () => {
    storeTokens('any', 'any');
    expect(isAuthenticated()).toBe(true);
    removeTokens();
    expect(isAuthenticated()).toBe(false);
  });

  it('cleans up legacy localStorage tokens', () => {
    // Simulate legacy tokens
    localStorage.setItem('nxentra_access', 'old-jwt');
    localStorage.setItem('nxentra_refresh', 'old-refresh');
    cleanupLegacyTokens();
    expect(localStorage.getItem('nxentra_access')).toBeNull();
    expect(localStorage.getItem('nxentra_refresh')).toBeNull();
  });
});
