import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { accountInquiryService } from "@/services/account-inquiry.service";
import type { AccountInquiryParams } from "@/types/account-inquiry";

export const accountInquiryKeys = {
  all: ["account-inquiry"] as const,
  detail: (code: string, params?: AccountInquiryParams) =>
    [...accountInquiryKeys.all, code, params || {}] as const,
};

export function useAccountInquiry(code: string, params?: AccountInquiryParams) {
  return useQuery({
    queryKey: accountInquiryKeys.detail(code, params),
    queryFn: async () => {
      const { data } = await accountInquiryService.get(code, params);
      return data;
    },
    enabled: !!code,
    placeholderData: keepPreviousData,
  });
}
