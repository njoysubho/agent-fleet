export interface LeaderDirectives {
  maxTasks: number | null;
  personas: string[];
}

const MAX_TASKS_RE = /^(?:max[_ ]tasks|task[_ ]count|tasks)\s*[:=]\s*(\d+)\s*$/im;
const MAX_TASKS_INLINE_RE = /\b(\d+)\s+tasks\b/i;
const PERSONAS_RE = /^(?:personas|roles|team)\s*[:=]\s*(.+?)\s*$/im;
const PERSONAS_INLINE_RE =
  /\b(?:having|with)\s+(?:an?\s+)?([a-zA-Z][a-zA-Z0-9_-]{1,20})\s+and\s+([a-zA-Z][a-zA-Z0-9_-]{1,20})\b/i;
const BLOCK_RE = /\[agentfleet\]([\s\S]*?)\[\/agentfleet\]/i;

export function parseLeaderDirectives(prompt: string): LeaderDirectives {
  const blockMatch = BLOCK_RE.exec(prompt);
  const scope = blockMatch ? blockMatch[1] : prompt;

  // --- max_tasks ---
  let maxTasks: number | null = null;
  const m = MAX_TASKS_RE.exec(scope);
  if (m) {
    const parsed = parseInt(m[1], 10);
    if (!isNaN(parsed) && parsed > 0) maxTasks = parsed;
  }
  if (maxTasks === null) {
    const mi = MAX_TASKS_INLINE_RE.exec(scope);
    if (mi) {
      const parsed = parseInt(mi[1], 10);
      if (!isNaN(parsed) && parsed > 0) maxTasks = parsed;
    }
  }

  // --- personas ---
  let personas: string[] = [];
  const p = PERSONAS_RE.exec(scope);
  if (p) {
    const raw = p[1];
    let parts = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (parts.length <= 1) {
      parts = raw.split(/\s+/).filter(Boolean);
    }
    const seen = new Set<string>();
    for (const item of parts) {
      const norm = item.trim().toLowerCase();
      if (!norm || seen.has(norm)) continue;
      seen.add(norm);
      personas.push(norm);
    }
  }

  if (personas.length === 0) {
    const inline = PERSONAS_INLINE_RE.exec(scope);
    if (inline) {
      const a = inline[1].trim().toLowerCase();
      const b = inline[2].trim().toLowerCase();
      if (a && b && a !== b) personas = [a, b];
    }
  }

  return { maxTasks, personas };
}

export function stripDirectiveBlock(prompt: string): string {
  return prompt.replace(BLOCK_RE, "").trim();
}
