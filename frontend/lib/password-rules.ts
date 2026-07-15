// Mirror of the canonical backend rules (backend/accounts/passwords.py).
// Semantics are kept in lockstep: "special" = anything that is not an ASCII
// letter/digit on BOTH sides, and length counts code points on BOTH sides
// (Array.from splits by code point; .length would count UTF-16 units and
// overcount astral characters vs Python's len()). Uppercase/number are
// ASCII-only here, i.e. stricter than the backend's isupper()/isdigit() —
// the safe direction: a password the checklist approves is never rejected
// by the server.
export interface PasswordRule {
  id: string;
  label: string;
  test: (password: string) => boolean;
}

export const passwordRules: PasswordRule[] = [
  { id: "length", label: "At least 8 characters", test: (password) => Array.from(password).length >= 8 },
  { id: "uppercase", label: "One uppercase letter (A–Z)", test: (password) => /[A-Z]/.test(password) },
  { id: "number", label: "One number (0–9)", test: (password) => /[0-9]/.test(password) },
  { id: "special", label: "One special character (e.g. !@#$%)", test: (password) => /[^A-Za-z0-9]/.test(password) },
];

export const passwordMeetsAllRules = (password: string) =>
  passwordRules.every((rule) => rule.test(password));

// One-line spellings of the rules for forms that don't render the checklist.
export const passwordRulesPlaceholder = "8+ chars, uppercase, number, special";
export const passwordRulesMessage =
  "Password must have at least 8 characters, including an uppercase letter, a number, and a special character";
