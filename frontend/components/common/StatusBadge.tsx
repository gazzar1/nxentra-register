import { useTranslation } from "next-i18next";
import { Badge } from "@/components/ui/badge";
import type { JournalEntryStatus } from "@/types/journal";
import type { AccountStatus } from "@/types/account";

type Status = JournalEntryStatus | AccountStatus;

const statusVariants: Record<Status, "default" | "secondary" | "success" | "warning" | "info" | "destructive"> = {
  // Journal entry statuses
  INCOMPLETE: "secondary",
  DRAFT: "warning",
  POSTED: "success",
  REVERSED: "info",
  // Account statuses
  ACTIVE: "success",
  INACTIVE: "secondary",
  LOCKED: "warning",
};

interface StatusBadgeProps {
  status: Status;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const { t } = useTranslation("common");

  const statusKey = status.toLowerCase() as keyof typeof statusVariants;
  const variant = statusVariants[status] || "default";
  const label = t(`status.${statusKey}`, status);

  return (
    <Badge variant={variant} className={className}>
      {label}
    </Badge>
  );
}
