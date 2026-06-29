import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { accountDrilldownService } from "@/services/account-drilldown.service";
import type { AccountDrilldownParams } from "@/types/account-drilldown";

export const accountDrilldownKeys = {
  all: ["account-drilldown"] as const,
  detail: (code: string, params?: AccountDrilldownParams) =>
    [...accountDrilldownKeys.all, code, params || {}] as const,
};

export function useAccountDrilldown(code: string, params?: AccountDrilldownParams) {
  return useQuery({
    queryKey: accountDrilldownKeys.detail(code, params),
    queryFn: async () => {
      const { data } = await accountDrilldownService.get(code, params);
      return data;
    },
    enabled: !!code,
    placeholderData: keepPreviousData,
  });
}
