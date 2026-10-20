"""Assemble the remember agent's input from the staged notes.

Usage:
    stage_notes.py <global_memory> <project_memory>

Runs between the two agents, so the remember agent has a single file to read: its
instructions, the raw notes staged earlier, and the current memory for
de-duplication.
"""
import sys

# Importing turn_digest must not drop a __pycache__ into the plugin directory: the
# installer ships whatever it finds there.
sys.dont_write_bytecode = True

import turn_digest as td  # noqa: E402


def main():
    global_memory, project_memory = sys.argv[1], sys.argv[2]

    staged = ""
    if td.staged_path().is_file():
        staged = td.staged_path().read_text().strip()

    parts = [
        (td.TEXT_DIR / "remember.md").read_text().strip(),
        "---",
        "## Staged notes",
        staged or "(nothing was staged for this turn - do nothing and finish)",
        "---",
        "## Files",
        f"- staging file, empty it when you are done: `{td.staged_path()}`",
        f"- **global** memory: `{global_memory}`",
        f"- **project** memory: `{project_memory}`",
        "---",
        "## Current memory",
        td.memory_section("global", global_memory),
        td.memory_section("project", project_memory),
    ]

    td.state_dir().mkdir(parents=True, exist_ok=True)
    td.remember_input_path().write_text("\n\n".join(parts) + "\n")


if __name__ == "__main__":
    main()
