// Phase 2 — small inline hint for sales lines: shows the on-hand qty at the
// selected warehouse, and a red warning when the line's requested quantity
// exceeds it (unless the item or company allows negative stock).
//
// Skipped (renders nothing) when itemId or warehouseId is missing.
//
// Visual:
//   "Available: 20"         ← green when qty <= available
//   "Available: 20 • short" ← red when qty > available and strictness on

import { useStockAvailability } from "@/queries/useInventory";
import { cn } from "@/lib/cn";

interface Props {
  itemId: number | null;
  warehouseId: number | null;
  qty: number;
}

export function LineAvailabilityHint({ itemId, warehouseId, qty }: Props) {
  const { data, isLoading } = useStockAvailability(itemId, warehouseId, qty);

  if (!itemId || !warehouseId) return null;
  if (isLoading || !data) return null;

  const available = parseFloat(data.qty_on_hand || "0");
  const negativeAllowed =
    data.allow_negative_stock || data.company_allow_negative_inventory;
  const short = qty > available;
  const shouldWarn = short && !negativeAllowed;

  return (
    <p
      className={cn(
        "text-[10px] mt-0.5 leading-tight font-mono text-end",
        shouldWarn ? "text-destructive" : "text-muted-foreground",
      )}
      title={
        shouldWarn
          ? `Only ${available} available at ${data.warehouse_code || "warehouse"}. ` +
            `Posting will be rejected unless this item allows negative stock.`
          : undefined
      }
    >
      Avail: {available.toLocaleString()}
      {shouldWarn && <span className="font-semibold"> · short {(qty - available).toLocaleString()}</span>}
    </p>
  );
}
