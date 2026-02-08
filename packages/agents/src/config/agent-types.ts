export interface AgentTypeConfig {
  tools: string[];
  defaultModel?: string;
}

export const AGENT_TYPES: Record<string, AgentTypeConfig> = {
  general: {
    tools: ["read", "write", "edit", "bash"],
  },
  explore: {
    tools: ["read", "bash"],
  },
  plan: {
    tools: ["read", "bash"],
  },
  bash: {
    tools: ["bash", "read"],
  },
};

export function getToolsForType(agentType: string): string[] {
  return (AGENT_TYPES[agentType] ?? AGENT_TYPES.general).tools;
}
