import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getErrorMessage } from '@/lib/api-client';
import { AxiosError, AxiosHeaders } from 'axios';

describe('getErrorMessage', () => {
  it('extracts string body', () => {
    const error = new AxiosError('fail');
    error.response = {
      data: 'Server Error',
      status: 500,
      statusText: 'Internal Server Error',
      headers: {},
      config: { headers: new AxiosHeaders() },
    };
    expect(getErrorMessage(error)).toBe('Server Error');
  });

  it('extracts error field', () => {
    const error = new AxiosError('fail');
    error.response = {
      data: { error: 'Account not found' },
      status: 404,
      statusText: 'Not Found',
      headers: {},
      config: { headers: new AxiosHeaders() },
    };
    expect(getErrorMessage(error)).toBe('Account not found');
  });

  it('extracts detail field', () => {
    const error = new AxiosError('fail');
    error.response = {
      data: { detail: 'Permission denied' },
      status: 403,
      statusText: 'Forbidden',
      headers: {},
      config: { headers: new AxiosHeaders() },
    };
    expect(getErrorMessage(error)).toBe('Permission denied');
  });

  it('extracts validation error', () => {
    const error = new AxiosError('fail');
    error.response = {
      data: { email: ['This field is required.'] },
      status: 400,
      statusText: 'Bad Request',
      headers: {},
      config: { headers: new AxiosHeaders() },
    };
    expect(getErrorMessage(error)).toBe('email: This field is required.');
  });

  it('handles plain Error', () => {
    expect(getErrorMessage(new Error('oops'))).toBe('oops');
  });

  it('handles unknown types', () => {
    expect(getErrorMessage(42)).toBe('An unexpected error occurred');
    expect(getErrorMessage(null)).toBe('An unexpected error occurred');
  });
});
