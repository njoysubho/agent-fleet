AGENT_TYPE_TOOLS: dict[str, list[str]] = {
    "general": ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch"],
    "explore": ["Read", "Glob", "Grep"],
    "plan": ["Read", "Glob", "Grep"],
    "bash": ["Bash", "Read"],
}
