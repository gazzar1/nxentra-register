// User and authentication types

export interface User {
  id: number;
  public_id: string;
  email: string;
  name: string;
  name_ar: string;
  is_active: boolean;
  is_staff?: boolean;
  is_superuser?: boolean;
  created_at: string;
  updated_at: string;
}

export interface Company {
  id: number;
  public_id: string;
  name: string;
  name_ar: string;
  slug: string;
  default_currency: string;
  fiscal_year_start_month: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CompanySettings {
  language: 'en' | 'ar';
  periods: number;
  current_period: number;
  thousand_separator: string;
  decimal_separator: string;
  decimal_places: number;
  date_format: string;
}

export type UserRole = 'OWNER' | 'ADMIN' | 'USER' | 'VIEWER';

export interface CompanyMembership {
  id: number;
  user: User;
  company: Company;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  permissions: string[];
}

export interface Permission {
  code: string;
  name: string;
  description?: string;
}

// Auth types
export interface AuthTokens {
  access: string;
  refresh: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export interface RegisterPayload {
  email: string;
  password: string;
  name?: string;
  company_name: string;
  default_currency?: string;
}

export interface ProfileResponse {
  user: User;
  company: Company & CompanySettings;
  membership: {
    role: UserRole;
    permissions: string[];
  };
}

// User management
export interface CreateUserPayload {
  email: string;
  password: string;
  name?: string;
  role: UserRole;
}

export interface UpdateUserPayload {
  name?: string;
  name_ar?: string;
}

export interface UpdateRolePayload {
  role: UserRole;
}
