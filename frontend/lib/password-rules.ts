// Mirror of the canonical backend rules (backend/accounts/passwords.py).
// The client checks are ASCII-scoped and therefore never accept a password
// the backend would reject; backend messages remain the fallback of record.
export interface PasswordRule {
  id: string;
  label: string;
  test: (password: string) => boolean;
}

export const passwordRules: PasswordRule[] = [
  { id: "length", label: "At least 8 characters", test: (password) => password.length >= 8 },
  { id: "uppercase", label: "One uppercase letter (A–Z)", test: (password) => /[A-Z]/.test(password) },
  { id: "number", label: "One number (0–9)", test: (password) => /[0-9]/.test(password) },
  { id: "special", label: "One special character (e.g. !@#$%)", test: (password) => /[^A-Za-z0-9]/.test(password) },
];

export const passwordMeetsAllRules = (password: string) =>
  passwordRules.every((rule) => rule.test(password));
