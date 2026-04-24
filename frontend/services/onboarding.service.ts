import apiClient from '@/lib/api-client';

export interface CoaTemplate {
  key: string;
  label: string;
  label_ar: string;
  description: string;
  description_ar: string;
  account_count: number;
}

export interface OnboardingStatus {
  onboarding_completed: boolean;
  coa_template: string;
  business_type: string;
  company: {
    name: string;
    name_ar: string;
    default_currency: string;
    fiscal_year_start_month: number;
    thousand_separator: string;
    decimal_separator: string;
    decimal_places: number;
    date_format: string;
  };
  templates: CoaTemplate[];
}

export interface OnboardingSetupPayload {
  // Business type
  business_type?: string;
  // Step 1
  company_name?: string;
  company_name_ar?: string;
  fiscal_year_start_month?: number;
  thousand_separator?: string;
  decimal_separator?: string;
  decimal_places?: number;
  date_format?: string;
  // Step 2
  fiscal_year?: number;
  num_periods?: number;
  current_period?: number;
  // Step 3
  coa_template?: string;
  // Step 4
  modules?: { key: string; is_enabled: boolean }[];
  // Shopify historical import
  import_mode?: "all" | "from_date" | "skip";
  import_from_date?: string; // ISO date (YYYY-MM-DD)
}

export const onboardingService = {
  getStatus: () =>
    apiClient.get<OnboardingStatus>('/onboarding/setup/').then((r) => r.data),

  complete: (payload: OnboardingSetupPayload) =>
    apiClient.post('/onboarding/setup/', payload).then((r) => r.data),

  saveDraft: (payload: Partial<OnboardingSetupPayload>) =>
    apiClient.patch('/onboarding/setup/', payload).then((r) => r.data),
};
