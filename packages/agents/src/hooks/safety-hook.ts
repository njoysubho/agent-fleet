const DANGEROUS_PATTERNS: RegExp[] = [
  /\brm\s+-rf\s+\/\b/,
  /\brm\s+-rf\s+--no-preserve-root\b/,
  /\bmkfs\.(ext2|ext3|ext4|xfs)\b/,
  /\bdd\s+if=.*\s+of=\/dev\/\w+/,
];

/**
 * Returns a rejection reason if the command matches a dangerous pattern,
 * or null if the command is safe.
 */
export function checkBashSafety(command: string): string | null {
  for (const pat of DANGEROUS_PATTERNS) {
    if (pat.test(command)) {
      return "Destructive command blocked by safety hook";
    }
  }
  return null;
}
