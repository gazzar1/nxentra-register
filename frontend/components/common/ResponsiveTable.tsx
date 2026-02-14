import { ReactNode } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/cn";

/**
 * Column definition for ResponsiveTable
 */
export interface ColumnDef<T> {
  /** Unique key for the column */
  key: string;
  /** Column header label */
  label: string;
  /** Function to render cell content */
  render: (item: T) => ReactNode;
  /** Hide on mobile (card view) */
  hideOnMobile?: boolean;
  /** Show as card header (primary info) */
  isCardHeader?: boolean;
  /** Show as card badge/status */
  isCardBadge?: boolean;
  /** Additional className for the cell */
  className?: string;
  /** Column width class for table view */
  width?: string;
}

interface ResponsiveTableProps<T> {
  /** Data to display */
  data: T[];
  /** Column definitions */
  columns: ColumnDef<T>[];
  /** Unique key extractor for each row */
  keyExtractor: (item: T) => string | number;
  /** Called when a row/card is clicked */
  onRowClick?: (item: T) => void;
  /** Empty state message */
  emptyMessage?: string;
  /** Additional table className */
  className?: string;
  /** Show card view below this breakpoint */
  mobileBreakpoint?: "sm" | "md" | "lg";
}

/**
 * ResponsiveTable - Switches between table and card view based on screen size
 *
 * On desktop: Shows a standard table layout
 * On mobile: Shows stacked cards with key information highlighted
 */
export function ResponsiveTable<T>({
  data,
  columns,
  keyExtractor,
  onRowClick,
  emptyMessage = "No data available",
  className,
  mobileBreakpoint = "md",
}: ResponsiveTableProps<T>) {
  const breakpointClass = {
    sm: "sm:block",
    md: "md:block",
    lg: "lg:block",
  }[mobileBreakpoint];

  const mobileHideClass = {
    sm: "sm:hidden",
    md: "md:hidden",
    lg: "lg:hidden",
  }[mobileBreakpoint];

  if (data.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        {emptyMessage}
      </div>
    );
  }

  // Find columns for card layout
  const headerColumn = columns.find((col) => col.isCardHeader);
  const badgeColumn = columns.find((col) => col.isCardBadge);
  const detailColumns = columns.filter(
    (col) => !col.isCardHeader && !col.isCardBadge && !col.hideOnMobile
  );

  return (
    <div className={className}>
      {/* Desktop: Table View */}
      <div className={cn("hidden", breakpointClass)}>
        <table className="w-full">
          <thead>
            <tr className="border-b">
              {columns.map((col) => (
                <th
                  key={col.key}
                  className={cn(
                    "text-start p-3 text-sm font-medium text-muted-foreground",
                    col.width,
                    col.className
                  )}
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((item) => (
              <tr
                key={keyExtractor(item)}
                onClick={() => onRowClick?.(item)}
                className={cn(
                  "border-b transition-colors hover:bg-muted/50",
                  onRowClick && "cursor-pointer"
                )}
              >
                {columns.map((col) => (
                  <td key={col.key} className={cn("p-3", col.className)}>
                    {col.render(item)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile: Card View */}
      <div className={cn("space-y-3", mobileHideClass)}>
        {data.map((item) => (
          <Card
            key={keyExtractor(item)}
            onClick={() => onRowClick?.(item)}
            className={cn(
              "transition-colors",
              onRowClick && "cursor-pointer hover:bg-muted/50"
            )}
          >
            <CardContent className="p-4">
              {/* Card Header: Primary info + Badge */}
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="font-medium">
                  {headerColumn ? headerColumn.render(item) : null}
                </div>
                {badgeColumn && (
                  <div className="flex-shrink-0">{badgeColumn.render(item)}</div>
                )}
              </div>

              {/* Card Details */}
              <div className="space-y-2 text-sm">
                {detailColumns.map((col) => (
                  <div
                    key={col.key}
                    className="flex justify-between items-center gap-4"
                  >
                    <span className="text-muted-foreground">{col.label}</span>
                    <span className={col.className}>{col.render(item)}</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

/**
 * MobileCardView - Standalone mobile card list component
 *
 * Use when you need just the card view without the table.
 */
interface MobileCardViewProps<T> {
  data: T[];
  keyExtractor: (item: T) => string | number;
  renderCard: (item: T) => ReactNode;
  onCardClick?: (item: T) => void;
  emptyMessage?: string;
  className?: string;
}

export function MobileCardView<T>({
  data,
  keyExtractor,
  renderCard,
  onCardClick,
  emptyMessage = "No data available",
  className,
}: MobileCardViewProps<T>) {
  if (data.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className={cn("space-y-3", className)}>
      {data.map((item) => (
        <div
          key={keyExtractor(item)}
          onClick={() => onCardClick?.(item)}
          className={cn(onCardClick && "cursor-pointer")}
        >
          {renderCard(item)}
        </div>
      ))}
    </div>
  );
}

/**
 * DataCard - Reusable card component for mobile data display
 */
interface DataCardProps {
  /** Primary title/identifier */
  title: ReactNode;
  /** Secondary text */
  subtitle?: ReactNode;
  /** Status badge */
  badge?: ReactNode;
  /** Key-value pairs to display */
  details?: Array<{ label: string; value: ReactNode }>;
  /** Actions (buttons) */
  actions?: ReactNode;
  /** Card click handler */
  onClick?: () => void;
  className?: string;
}

export function DataCard({
  title,
  subtitle,
  badge,
  details,
  actions,
  onClick,
  className,
}: DataCardProps) {
  return (
    <Card
      onClick={onClick}
      className={cn(
        "transition-colors",
        onClick && "cursor-pointer hover:bg-muted/50",
        className
      )}
    >
      <CardContent className="p-4">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="font-medium truncate">{title}</div>
            {subtitle && (
              <div className="text-sm text-muted-foreground mt-0.5">
                {subtitle}
              </div>
            )}
          </div>
          {badge && <div className="flex-shrink-0">{badge}</div>}
        </div>

        {/* Details */}
        {details && details.length > 0 && (
          <div className="mt-3 pt-3 border-t space-y-2 text-sm">
            {details.map((detail, i) => (
              <div key={i} className="flex justify-between items-center gap-4">
                <span className="text-muted-foreground">{detail.label}</span>
                <span>{detail.value}</span>
              </div>
            ))}
          </div>
        )}

        {/* Actions */}
        {actions && (
          <div className="mt-3 pt-3 border-t flex justify-end gap-2">
            {actions}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
