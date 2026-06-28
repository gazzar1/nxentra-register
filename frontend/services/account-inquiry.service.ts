import apiClient from "@/lib/api-client";
import type {
  AccountInquiryParams,
  AccountInquiryResponse,
} from "@/types/account-inquiry";

// A137 — read-only GL account drilldown. Mirrors accounts.service.ts.
export const accountInquiryService = {
  get: (code: string, params?: AccountInquiryParams) =>
    apiClient.get<AccountInquiryResponse>(
      `/accounting/accounts/${code}/inquiry/`,
      { params }
    ),
};
