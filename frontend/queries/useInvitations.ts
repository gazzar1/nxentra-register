import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { invitationsService, type InvitationCreatePayload } from '@/services/invitations.service';

// Query keys factory
export const invitationKeys = {
  all: ['invitations'] as const,
  lists: () => [...invitationKeys.all, 'list'] as const,
  details: () => [...invitationKeys.all, 'detail'] as const,
  detail: (id: number) => [...invitationKeys.details(), id] as const,
  info: (token: string) => [...invitationKeys.all, 'info', token] as const,
};

// List pending invitations
export function useInvitations() {
  return useQuery({
    queryKey: invitationKeys.lists(),
    queryFn: async () => {
      const { data } = await invitationsService.list();
      return data;
    },
  });
}

// Get a specific invitation
export function useInvitation(id: number) {
  return useQuery({
    queryKey: invitationKeys.detail(id),
    queryFn: async () => {
      const { data } = await invitationsService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

// Get invitation info by token (no auth required)
export function useInvitationInfo(token: string | null) {
  return useQuery({
    queryKey: invitationKeys.info(token || ''),
    queryFn: async () => {
      if (!token) throw new Error('No token provided');
      const { data } = await invitationsService.getInfo(token);
      return data;
    },
    enabled: !!token,
    retry: false,
  });
}

// Create invitation
export function useCreateInvitation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: InvitationCreatePayload) => invitationsService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: invitationKeys.lists() });
    },
  });
}

// Cancel invitation
export function useCancelInvitation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason?: string }) =>
      invitationsService.cancel(id, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: invitationKeys.lists() });
    },
  });
}

// Resend invitation
export function useResendInvitation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => invitationsService.resend(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: invitationKeys.detail(id) });
      queryClient.invalidateQueries({ queryKey: invitationKeys.lists() });
    },
  });
}

// Accept invitation (no auth required)
export function useAcceptInvitation() {
  return useMutation({
    mutationFn: (payload: { token: string; password: string; name?: string }) =>
      invitationsService.accept(payload),
  });
}
