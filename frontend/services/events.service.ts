import apiClient from '@/lib/api-client';

// Types for event audit API
export interface BusinessEvent {
  id: string;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  sequence: number;
  company_sequence: number;
  occurred_at: string;
  recorded_at: string;
  caused_by_user_email: string | null;
  origin: 'human' | 'batch' | 'api' | 'system';
  payload_storage: 'inline' | 'external' | 'chunked';
  payload_hash: string | null;
}

export interface BusinessEventDetail extends BusinessEvent {
  idempotency_key: string | null;
  data: Record<string, unknown>;
  resolved_data: Record<string, unknown>;
  metadata: Record<string, unknown>;
  schema_version: number;
  caused_by_user: number | null;
  caused_by_event: string | null;
  caused_by_event_id: string | null;
  child_event_ids: string[];
  external_source: string | null;
  external_id: string | null;
  payload_ref: string | null;
  payload_ref_info: PayloadRefInfo | null;
}

export interface PayloadRefInfo {
  id: string;
  content_hash: string;
  size_bytes: number;
  compression: string;
  created_at: string;
}

export interface EventCausationChain {
  event: BusinessEvent;
  parent: BusinessEvent | null;
  children: BusinessEvent[];
  chain_depth: number;
}

export interface AggregateHistory {
  aggregate_type: string;
  aggregate_id: string;
  event_count: number;
  first_event_at: string | null;
  last_event_at: string | null;
  events: BusinessEvent[];
}

export interface JournalEventMapping {
  journal_public_id: string;
  event_count: number;
  events: BusinessEvent[];
}

export interface IntegrityCheckResult {
  total_events: number;
  verified_events: number;
  external_payload_count: number;
  chunked_event_count: number;
  inline_event_count: number;
  total_payload_bytes: number;
  payload_errors: IntegrityError[];
  sequence_gaps: SequenceGap[];
  is_valid: boolean;
}

export interface IntegrityError {
  error_type: string;
  message: string;
  event_id: string | null;
  details: Record<string, unknown>;
}

export interface SequenceGap {
  start: number;
  end: number;
  missing_count: number;
}

export interface IntegritySummary {
  total_events: number;
  max_sequence: number;
  has_potential_gaps: boolean;
  storage_breakdown: Record<string, number>;
  origin_breakdown: Record<string, number>;
  external_payload_count: number;
  chunked_event_count: number;
}

export interface EventBookmark {
  id: string;
  consumer_name: string;
  company: string;
  company_name: string;
  last_event: string | null;
  last_event_id: string | null;
  last_processed_at: string | null;
  is_paused: boolean;
  error_count: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface EventListParams {
  event_type?: string;
  aggregate_type?: string;
  aggregate_id?: string;
  origin?: string;
  occurred_at__gte?: string;
  occurred_at__lte?: string;
}

export const eventsService = {
  // List events with optional filters
  list: (params?: EventListParams) =>
    apiClient.get<BusinessEvent[]>('/events/', { params }),

  // Get event detail
  get: (id: string) =>
    apiClient.get<BusinessEventDetail>(`/events/${id}/`),

  // Get causation chain for an event
  getChain: (eventId: string) =>
    apiClient.get<EventCausationChain>(`/events/${eventId}/chain/`),

  // Get event history for an aggregate
  getAggregateHistory: (aggregateType: string, aggregateId: string) =>
    apiClient.get<AggregateHistory>(`/events/aggregate/${aggregateType}/${aggregateId}/`),

  // Get events for a journal entry
  getJournalEvents: (journalPublicId: string) =>
    apiClient.get<JournalEventMapping>(`/events/journal/${journalPublicId}/`),

  // Run full integrity check (admin only)
  runIntegrityCheck: () =>
    apiClient.get<IntegrityCheckResult>('/events/integrity-check/'),

  // Get quick integrity summary
  getIntegritySummary: () =>
    apiClient.get<IntegritySummary>('/events/integrity-summary/'),

  // Get projection bookmarks
  getBookmarks: () =>
    apiClient.get<EventBookmark[]>('/events/bookmarks/'),
};
