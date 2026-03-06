import '@testing-library/jest-dom/vitest';

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value; },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (index: number) => Object.keys(store)[index] ?? null,
  };
})();

Object.defineProperty(window, 'localStorage', { value: localStorageMock });

// Mock window.location
Object.defineProperty(window, 'location', {
  value: { href: '', assign: vi.fn(), replace: vi.fn(), reload: vi.fn() },
  writable: true,
});

// Reset mocks between tests
beforeEach(() => {
  localStorageMock.clear();
  vi.clearAllMocks();
});
