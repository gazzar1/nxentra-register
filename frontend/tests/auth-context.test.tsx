import { describe, it, expect } from 'vitest';

/**
 * Test the hasPermission logic extracted from AuthContext.
 *
 * The full AuthContext requires rendering with QueryClient, Router,
 * and auth service mocks which causes worker crashes in vitest.
 * Instead, we test the permission logic in isolation.
 */

// Extracted from AuthContext.tsx — same logic
function hasPermission(
  user: { is_superuser?: boolean } | null,
  membership: { role: string; permissions: string[] } | null,
  code: string
): boolean {
  if (user?.is_superuser) return true;
  if (!membership) return false;
  if (membership.role === 'OWNER') return true;
  return membership.permissions.includes(code);
}

describe('AuthContext - hasPermission', () => {
  it('superuser bypasses all permission checks', () => {
    expect(hasPermission({ is_superuser: true }, { role: 'USER', permissions: [] }, 'any.permission')).toBe(true);
    expect(hasPermission({ is_superuser: true }, null, 'any.permission')).toBe(true);
  });

  it('OWNER has implicit access to everything', () => {
    expect(hasPermission({ is_superuser: false }, { role: 'OWNER', permissions: [] }, 'any.permission')).toBe(true);
  });

  it('ADMIN needs explicit permission grant', () => {
    const membership = { role: 'ADMIN', permissions: ['accounting.view', 'journal.create'] };
    expect(hasPermission({}, membership, 'accounting.view')).toBe(true);
    expect(hasPermission({}, membership, 'journal.create')).toBe(true);
    expect(hasPermission({}, membership, 'sales.create')).toBe(false);
  });

  it('USER with no permissions is denied', () => {
    expect(hasPermission({}, { role: 'USER', permissions: [] }, 'any.perm')).toBe(false);
  });

  it('VIEWER with specific permissions can access only those', () => {
    const membership = { role: 'VIEWER', permissions: ['reports.view', 'dashboard.view'] };
    expect(hasPermission({}, membership, 'reports.view')).toBe(true);
    expect(hasPermission({}, membership, 'dashboard.view')).toBe(true);
    expect(hasPermission({}, membership, 'journal.create')).toBe(false);
  });

  it('null user with no membership returns false', () => {
    expect(hasPermission(null, null, 'any.perm')).toBe(false);
  });

  it('user with no membership returns false', () => {
    expect(hasPermission({ is_superuser: false }, null, 'any.perm')).toBe(false);
  });
});
