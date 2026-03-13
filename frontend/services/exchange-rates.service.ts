import apiClient from "@/lib/api-client";

export interface ExchangeRate {
  id: number;
  public_id: string;
  from_currency: string;
  to_currency: string;
  rate: string;
  effective_date: string;
  rate_type: string;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface ExchangeRateCreatePayload {
  from_currency: string;
  to_currency: string;
  rate: string;
  effective_date: string;
  rate_type?: string;
  source?: string;
}

export interface ExchangeRateLookup {
  from_currency: string;
  to_currency: string;
  date: string;
  rate_type: string;
  rate: string | null;
  message?: string;
}

export const exchangeRatesService = {
  list: (params?: {
    from_currency?: string;
    to_currency?: string;
    rate_type?: string;
  }) => apiClient.get<ExchangeRate[]>("/accounting/exchange-rates/", { params }),

  get: (id: number) =>
    apiClient.get<ExchangeRate>(`/accounting/exchange-rates/${id}/`),

  create: (data: ExchangeRateCreatePayload) =>
    apiClient.post<ExchangeRate & { created: boolean }>(
      "/accounting/exchange-rates/",
      data
    ),

  update: (id: number, data: Partial<ExchangeRateCreatePayload>) =>
    apiClient.put<ExchangeRate>(`/accounting/exchange-rates/${id}/`, data),

  delete: (id: number) =>
    apiClient.delete(`/accounting/exchange-rates/${id}/`),

  lookup: (params: {
    from_currency: string;
    to_currency: string;
    date: string;
    rate_type?: string;
  }) =>
    apiClient.get<ExchangeRateLookup>("/accounting/exchange-rates/lookup/", {
      params,
    }),
};
