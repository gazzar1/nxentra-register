import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";

interface TableSkeletonProps {
  /** Number of rows to display */
  rows?: number;
  /** Number of columns to display */
  columns?: number;
  /** Show header row */
  showHeader?: boolean;
  /** Additional className for the container */
  className?: string;
}

/**
 * TableSkeleton - Loading placeholder for table content
 *
 * Displays animated skeleton rows that match table layout,
 * providing better perceived performance during data loading.
 */
export function TableSkeleton({
  rows = 5,
  columns = 4,
  showHeader = true,
  className,
}: TableSkeletonProps) {
  return (
    <div className={cn("w-full", className)}>
      {/* Header skeleton */}
      {showHeader && (
        <div className="flex gap-4 pb-4 border-b mb-4">
          {Array.from({ length: columns }).map((_, i) => (
            <Skeleton
              key={`header-${i}`}
              className={cn(
                "h-4",
                i === 0 ? "w-20" : i === columns - 1 ? "w-24" : "flex-1"
              )}
            />
          ))}
        </div>
      )}

      {/* Row skeletons */}
      <div className="space-y-3">
        {Array.from({ length: rows }).map((_, rowIndex) => (
          <div key={rowIndex} className="flex gap-4 items-center py-2">
            {Array.from({ length: columns }).map((_, colIndex) => (
              <Skeleton
                key={`${rowIndex}-${colIndex}`}
                className={cn(
                  "h-5",
                  colIndex === 0
                    ? "w-20"
                    : colIndex === columns - 1
                    ? "w-24"
                    : "flex-1"
                )}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

interface CardSkeletonProps {
  /** Number of cards to display */
  count?: number;
  /** Additional className */
  className?: string;
}

/**
 * CardSkeleton - Loading placeholder for card-based layouts
 */
export function CardSkeleton({ count = 3, className }: CardSkeletonProps) {
  return (
    <div className={cn("space-y-4", className)}>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="rounded-lg border bg-card p-4 space-y-3"
        >
          <div className="flex justify-between items-start">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-6 w-16 rounded-full" />
          </div>
          <Skeleton className="h-4 w-3/4" />
          <div className="flex justify-between items-center pt-2">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-20" />
          </div>
        </div>
      ))}
    </div>
  );
}

interface FormSkeletonProps {
  /** Number of fields to display */
  fields?: number;
  /** Additional className */
  className?: string;
}

/**
 * FormSkeleton - Loading placeholder for form content
 */
export function FormSkeleton({ fields = 4, className }: FormSkeletonProps) {
  return (
    <div className={cn("space-y-6", className)}>
      {Array.from({ length: fields }).map((_, i) => (
        <div key={i} className="space-y-2">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-10 w-full" />
        </div>
      ))}
      <div className="flex gap-3 pt-4">
        <Skeleton className="h-10 w-24" />
        <Skeleton className="h-10 w-20" />
      </div>
    </div>
  );
}

interface PageSkeletonProps {
  /** Show page header skeleton */
  showHeader?: boolean;
  /** Show filter bar skeleton */
  showFilters?: boolean;
  /** Content type */
  contentType?: "table" | "cards" | "form";
  /** Additional className */
  className?: string;
}

/**
 * PageSkeleton - Full page loading placeholder
 */
export function PageSkeleton({
  showHeader = true,
  showFilters = false,
  contentType = "table",
  className,
}: PageSkeletonProps) {
  return (
    <div className={cn("space-y-6", className)}>
      {/* Page header skeleton */}
      {showHeader && (
        <div className="space-y-2">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-72" />
        </div>
      )}

      {/* Filter bar skeleton */}
      {showFilters && (
        <div className="flex gap-4 flex-wrap">
          <Skeleton className="h-10 w-40" />
          <Skeleton className="h-10 w-40" />
          <Skeleton className="h-10 w-24" />
        </div>
      )}

      {/* Content skeleton */}
      <div className="rounded-lg border bg-card p-6">
        {contentType === "table" && <TableSkeleton />}
        {contentType === "cards" && <CardSkeleton />}
        {contentType === "form" && <FormSkeleton />}
      </div>
    </div>
  );
}
