"""Writes the physics subagent for Claude Code (.claude/agents) and the Agent Skill (.claude/skills) from
physics.md, so the three stay the same.  python -m spinmate_mcp.agent_files"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDE = (Path(__file__).resolve().parent / "physics.md").read_text()
HEADER = "<!-- generated from spinmate_mcp/physics.md by python -m spinmate_mcp.agent_files; edit that file -->\n\n"

FILES = {
    ".claude/agents/spinmate-physicist.md": (
        "---\nname: spinmate-physicist\n"
        "description: Spintronics simulation specialist. Use it to design, run and interpret two-sublattice "
        "macrospin simulations (antiferromagnets, FeRh, exchange-coupled ferromagnets, spin-orbit torque "
        "switching) with the SpinMate MCP tools.\n---\n\n"),
    ".claude/skills/spinmate-physics/SKILL.md": (
        "---\nname: spinmate-physics\n"
        "description: Design, run and interpret two-sublattice macrospin simulations with the SpinMate MCP "
        "tools. Use when the user asks about antiferromagnet, FeRh or spin-orbit torque dynamics, or about "
        "SpinMate runs, sweeps, figures and reports.\n---\n\n"),
}


def render():
    return {path: front + HEADER + GUIDE for path, front in FILES.items()}


def main():
    for path, text in render().items():
        f = ROOT / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
