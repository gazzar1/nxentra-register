import apiClient from '@/lib/api-client';

// Types
export interface Invitation {
  id: number;
  public_id: string;
  email: string;
  name: string;
  role: string;
  status: 'PENDING' | 'ACCEPTED' | 'EXPIRED' | 'CANCELLED';
  company_ids: number[];
  permission_codes: string[];
  invited_by_email: string | null;
  invited_by_name: string | null;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
}

export interface InvitationCreatePayload {
  email: string;
  name?: string;
  role?: string;
  company_ids?: number[];
  permission_codes?: string[];
}

export interface InvitationInfo {
  email: string;
  name: string;
  company_name: string;
  invited_by_name: string | null;
  invited_by_email: string | null;
  role: string;
  expires_at: string;
}

export interface AcceptInvitationPayload {
  token: string;
  password: string;
  name?: string;
}

export interface AcceptInvitationResponse {
  user: {
    id: number;
    public_id: string;
    email: string;
    name: string;
  };
  company: {
    id: number;
    public_id: string;
    name: string;
  };
  tokens: {
    access: string;
    refresh: string;
  };
}

// Service functions
export const invitationsService = {
  /**
   * List pending invitations for the current company
   */
  list: () =>
    apiClient.get<{ count: number; invitations: Invitation[] }>('/invitations/'),

  /**
   * Get a specific invitation by ID
   */
  get: (id: number) =>
    apiClient.get<Invitation>(`/invitations/${id}/`),

  /**
   * Create a new invitation
   */
  create: (data: InvitationCreatePayload) =>
    apiClient.post<Invitation>('/invitations/', data),

  /**
   * Cancel an invitation
   */
  cancel: (id: number, reason?: string) =>
    apiClient.delete(`/invitations/${id}/`, { data: { reason } }),

  /**
   * Resend an invitation email
   */
  resend: (id: number) =>
    apiClient.post<{ email_sent: boolean; new_expiry: string }>(`/invitations/${id}/resend/`),

  /**
   * Get invitation info by token (no auth required)
   */
  getInfo: (token: string) =>
    apiClient.get<InvitationInfo>(`/invitations/info/?token=${encodeURIComponent(token)}`),

  /**
   * Accept an invitation (no auth required)
   */
  accept: (data: AcceptInvitationPayload) =>
    apiClient.post<AcceptInvitationResponse>('/invitations/accept/', data),
};

export default invitationsService;
