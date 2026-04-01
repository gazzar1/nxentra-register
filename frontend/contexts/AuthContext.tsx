import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  ReactNode,
} from 'react';
import { useRouter } from 'next/router';
import {
  isAuthenticated as checkAuthFlag,
  setAuthenticated,
  cleanupLegacyTokens,
} from '@/lib/auth-storage';
import { authService } from '@/services/auth.service';
import type { User, Company, CompanySettings, UserRole } from '@/types/user';

interface AuthState {
  user: User | null;
  company: (Company & CompanySettings) | null;
  membership: {
    role: UserRole;
    permissions: string[];
  } | null;
  isLoading: boolean;
  isAuthenticated: boolean;
}

interface AuthContextType extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchCompany: (companyId: number) => Promise<void>;
  refreshProfile: () => Promise<void>;
  hasPermission: (code: string) => boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    company: null,
    membership: null,
    isLoading: true,
    isAuthenticated: false,
  });
  const router = useRouter();

  const fetchProfile = useCallback(async () => {
    try {
      const { data } = await authService.getProfile();
      setAuthenticated(true);
      setState({
        user: data.user,
        company: data.company,
        membership: data.membership,
        isLoading: false,
        isAuthenticated: true,
      });

      // Update locale based on company language preference
      if (data.company.language && data.company.language !== router.locale) {
        router.push(router.pathname, router.asPath, {
          locale: data.company.language,
        });
      }

      return data;
    } catch {
      setAuthenticated(false);
      setState({
        user: null,
        company: null,
        membership: null,
        isLoading: false,
        isAuthenticated: false,
      });
      return null;
    }
  }, [router]);

  useEffect(() => {
    // Clean up legacy localStorage tokens from pre-cookie auth
    cleanupLegacyTokens();

    const initAuth = async () => {
      if (checkAuthFlag()) {
        await fetchProfile();
      } else {
        setState((prev) => ({ ...prev, isLoading: false }));
      }
    };

    initAuth();
  }, [fetchProfile]);

  const login = async (email: string, password: string) => {
    // Backend sets HttpOnly cookies on successful login
    const { data } = await authService.login({ email, password });
    setAuthenticated(true);
    const profile = await fetchProfile();
    // Redirect new owners to onboarding setup if not completed
    if (
      profile?.company &&
      !profile.company.onboarding_completed &&
      profile.membership?.role === 'OWNER'
    ) {
      router.push('/onboarding/setup');
    } else {
      router.push('/dashboard');
    }
  };

  const logout = async () => {
    try {
      // Backend reads refresh from cookie, blacklists it, and clears cookies
      await authService.logout();
    } catch {
      // Ignore logout errors
    }
    setAuthenticated(false);
    setState({
      user: null,
      company: null,
      membership: null,
      isLoading: false,
      isAuthenticated: false,
    });
    // Use hard redirect to avoid race with axios interceptor's window.location.href
    window.location.href = '/login';
  };

  const switchCompany = async (companyId: number) => {
    // Backend sets new tenant-bound cookies in the response
    await authService.switchCompany(companyId);
    setAuthenticated(true);
    // Refresh profile to update the state
    await fetchProfile();
  };

  const refreshProfile = async () => {
    await fetchProfile();
  };

  const hasPermission = (code: string): boolean => {
    // Superusers have all permissions
    if (state.user?.is_superuser) return true;
    if (!state.membership) return false;
    // Owner has all permissions
    if (state.membership.role === 'OWNER') return true;
    return state.membership.permissions.includes(code);
  };

  return (
    <AuthContext.Provider
      value={{
        ...state,
        login,
        logout,
        switchCompany,
        refreshProfile,
        hasPermission,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}
