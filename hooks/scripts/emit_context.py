"""Emit the injected context for the UserPromptSubmit hook.

Usage: emit_context.py <global_path> <project_path>

Prints the resolved memory file paths, the current contents of both memory files
and a capture reminder, as the hook's `additionalContext`.

Only `UserPromptSubmit` injects `additionalContext` in the SDK (see
hooks/conversation_hooks.py: `run_session_start` drops it, and `Stop` only uses it
as feedback when it denies a stop). Everything the agent must see therefore goes
here. The prose lives in hooks/text/.
"""
import json
import sys

# Importing a sibling must not drop a __pycache__ into the plugin directory: the
# installer ships every file it finds, so a .pyc here ends up in every install.
sys.dont_write_bytecode = True

from templates import render  # noqa: E402


def read(path):
    try:
        with open(path) as handle:
            return handle.read().rstrip()
    except OSError:
        return ""


def main():
    global_path, project_path = sys.argv[1], sys.argv[2]

    # Always emitted, even before any memory exists: the agent needs the paths.
    parts = [
        render(
            "memory-paths.md",
            global_path=global_path,
            project_path=project_path,
        ).strip()
    ]

    global_text = read(global_path)
    if global_text:
        parts.append(render("global-section.md", path=global_path, body=global_text))
    project_text = read(project_path)
    if project_text:
        parts.append(render("project-section.md", path=project_path, body=project_text))

    parts.append(render("reminder.md").strip())
    print(json.dumps({"additionalContext": "\n\n".join(parts)}))


if __name__ == "__main__":
    main()
