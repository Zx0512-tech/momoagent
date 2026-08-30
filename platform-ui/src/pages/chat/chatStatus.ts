export const TERMINAL_STATUSES = [
  "SUCCEEDED",
  "COMPLETED_DIAGNOSTIC",
  "FAILED",
  "CANCELLED",
  "UNSUPPORTED"
] as const;

export function isTerminalStatus(status: string | undefined): boolean {
  return status ? TERMINAL_STATUSES.includes(status as typeof TERMINAL_STATUSES[number]) : false;
}
