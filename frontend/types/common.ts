// Common types used across the application

export interface ApiError {
  message: string;
  code?: string;
  field?: string;
  details?: Record<string, string[]>;
}

export interface ApiResponse<T> {
  data: T;
  message?: string;
}

export interface PaginatedResponse<T> {
  results: T[];
  count: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface PaginationParams {
  page?: number;
  page_size?: number;
  ordering?: string;
  search?: string;
}

export interface CommandResult<T = unknown> {
  success: boolean;
  data?: T;
  error?: string;
  event?: {
    id: string;
    event_type: string;
  };
}

// Locale type
export type Locale = 'en' | 'ar';

// Status badge variants
export type StatusVariant = 'default' | 'success' | 'warning' | 'error' | 'info';

// Table sorting
export interface SortConfig {
  key: string;
  direction: 'asc' | 'desc';
}

// Date range filter
export interface DateRange {
  from: string;
  to: string;
}
