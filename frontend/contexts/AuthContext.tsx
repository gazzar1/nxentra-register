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
  getAccessToken,
  getRefreshToken,
  storeTokens,
  removeTokens,
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
      removeTokens();
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
    const initAuth = async () => {
      const token = getAccessToken();
      if (token) {
        await fetchProfile();
      } else {
        setState((prev) => ({ ...prev, isLoading: false }));
      }
    };

    initAuth();
  }, [fetchProfile]);

  const login = async (email: string, password: string) => {
    const { data } = await authService.login({ email, password });
    storeTokens(data.access, data.refresh);
    await fetchProfile();
    router.push('/dashboard');
  };

  const logout = async () => {
    try {
      const refreshToken = getRefreshToken();
      if (refreshToken) {
        await authService.logout(refreshToken);
      }
    } catch {
      // Ignore logout errors
    }
    removeTokens();
    setState({
      user: null,
      company: null,
      membership: null,
      isLoading: false,
      isAuthenticated: false,
    });
    router.push('/login');
  };

  const switchCompany = async (companyId: number) => {
    const { data } = await authService.switchCompany(companyId);
    // Store the new tenant-bound tokens
    storeTokens(data.tokens.access, data.tokens.refresh);
    // Refresh profile to update the state
    await fetchProfile();
  };

  const refreshProfile = async () => {
    await fetchProfile();
  };

  const hasPermission = (code: string): boolean => {
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
