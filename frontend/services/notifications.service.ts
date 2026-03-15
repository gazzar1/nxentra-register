import apiClient from "@/lib/api-client";

export interface Notification {
  id: number;
  title: string;
  message: string;
  level: "INFO" | "WARNING" | "ERROR" | "SUCCESS";
  is_read: boolean;
  link: string;
  source_module: string;
  created_at: string;
}

export interface NotificationListResponse {
  notifications: Notification[];
  unread_count: number;
}

export const notificationsService = {
  list: () =>
    apiClient.get<NotificationListResponse>("/notifications/"),

  markAsRead: (id: number) =>
    apiClient.post(`/notifications/${id}/read/`),

  markAllAsRead: () =>
    apiClient.post("/notifications/read-all/"),
};
