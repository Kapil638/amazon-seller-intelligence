const ASIN_PATTERN = /^[A-Z0-9]{10}$/;

export function normalizeAsin(value: string): string {
  return value.trim().toUpperCase();
}

export function isValidAsin(value: string): boolean {
  return ASIN_PATTERN.test(normalizeAsin(value));
}
