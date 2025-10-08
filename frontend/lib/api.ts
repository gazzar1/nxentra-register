import axios from "axios";

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api",
  withCredentials: true
});

export interface AuthResponse {
  access: string;
  refresh: string;
}

export interface RegistrationPayload {
  email: string;
  name: string;
  password: string;
  company_name: string;
  currency: string;
  language: string;
  periods: number;
  current_period: number;
  thousand_separator: string;
  decimal_places: number;
  decimal_separator: string;
  date_format: string;
}

export async function register(payload: RegistrationPayload): Promise<AuthResponse> {
  const response = await api.post<AuthResponse>('/auth/register/', payload);
  return response.data;
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  const response = await api.post<AuthResponse>('/auth/login/', { email, password });
  return response.data;
}

export async function logout(refresh: string) {
  await api.post('/auth/logout/', { refresh });
}

export interface ProfileResponse {
  user: {
    id: number;
    email: string;
    name: string;
  };
  company: {
    name: string;
    currency: string;
    language: string;
    periods: number;
    current_period: number;
    thousand_separator: string;
    decimal_places: number;
    decimal_separator: string;
    date_format: string;
  };
}

export async function getProfile(accessToken: string): Promise<ProfileResponse> {
  const response = await api.get<ProfileResponse>('/profile/', {
    headers: {
      Authorization: `Bearer ${accessToken}`
    }
  });
  return response.data;
}
