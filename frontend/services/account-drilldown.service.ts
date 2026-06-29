import apiClient from "@/lib/api-client";
import type {
  AccountDrilldownParams,
  AccountDrilldownResponse,
} from "@/types/account-drilldown";

// A137 — read-only GL account drilldown. Mirrors accounts.service.ts.
// Note: distinct from `accountInquiryService` in periods.service.ts, which
// backs the Reports "Account Inquiry" line-search report.
export const accountDrilldownService = {
  get: (code: string, params?: AccountDrilldownParams) =>
    apiClient.get<AccountDrilldownResponse>(
      `/accounting/accounts/${code}/drilldown/`,
      { params }
    ),
};
