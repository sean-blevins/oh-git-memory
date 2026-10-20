"""Build the decide agent's input, and stage anything that is already decided.

Usage:
    turn_digest.py --digest <global_memory> <project_memory>
    turn_digest.py --mark
    turn_digest.py --arm [global|project]   # debug: stage the next turn instead
    turn_digest.py --disarm

The transcript lives *outside* the workspace, one JSON file per event:

    $OH_CONVERSATIONS_PATH/<conversation-id>/events/event-<N>-<uuid>.json

`--mark` records the end of the transcript and is called from the
UserPromptSubmit hook; `--digest` then covers the events after that mark, stepping
back over the user's own message (see `window`). Without a mark it falls back to
the tail.

Three things are staged *here*, deterministically, without asking the decide agent:

* the `remember` command's payload, when the user invoked it this turn;
* the force token, when the user typed `/oh-git-memory:force` this turn;
* the whole turn, when that debug force-save flag is armed for this turn.

All three land in the staging file the remember agent reads. Staging them in a
command hook is what makes them deterministic - the decide agent is told they are
already there and has nothing to add.

Entries are rendered from the events the SDK actually writes, which is why this
dispatches on `kind`: a real ActionEvent carries no `llm_message` but does carry
`thought` (what the agent said as it acted) and `summary`. Reading only the closing
MessageEvent would hand both agents the word "Done." and nothing else.
"""
import glob
import json
import os
import re
import sys
from pathlib import Path

TEXT_DIR = Path(__file__).resolve().parent.parent / "text"

MAX_TOTAL_CHARS = 20000  # transcript budget handed to the decide agent
MAX_ENTRY_CHARS = 2500  # per message / narrated step
EXPLICIT_MAX_CHARS = 1200  # payload the user asked to remember
FORCE_MAX_CHARS = 12000  # a forced whole turn, deliberately not capped tight
TAIL_EVENTS = 80  # events used when no mark has been recorded
STATE_DIR_NAME = ".ohgm"  # gitignored scratch space inside the clone
MARK_NAME = ".turn-mark"
DIGEST_NAME = ".turn-digest.md"  # decide agent's input
REMEMBER_INPUT_NAME = ".remember-input.md"  # remember agent's input
STAGING_NAME = "to-remember.md"  # raw notes passed between the two agents

# Debug force-save, for arming from outside a message. The prompt hook promotes it
# so it applies to the turn following the arming, not the turn it was armed in.
FORCE_PENDING_NAME = ".force-save"
FORCE_READY_NAME = ".force-next"

# Trigger keywords. The SDK derives the command's trigger from its plugin name.
COMMAND_TOKEN = "/oh-git-memory:remember"
FORCE_TOKEN = "/oh-git-memory:force"

# Action fields worth quoting, most specific first.
ACTION_FIELDS = ("command", "path", "file_text", "command_str")


def memory_dir():
    return Path(os.environ.get("OH_GIT_MEMORY_DIR") or Path.home() / ".oh-git-memory")


def state_dir():
    """Scratch space inside the clone, excluded by the .gitignore the bootstrap writes."""
    return memory_dir() / STATE_DIR_NAME


def staged_path():
    return state_dir() / STAGING_NAME


def remember_input_path():
    return state_dir() / REMEMBER_INPUT_NAME


def events_dir():
    base = os.environ.get("OH_CONVERSATIONS_PATH", "")
    if not base or not os.path.isdir(base):
        return None
    session = os.environ.get("OPENHANDS_SESSION_ID", "")
    if session:
        candidate = os.path.join(base, session, "events")
        if os.path.isdir(candidate):
            return candidate
    # The session id need not name the directory; fall back to the newest one.
    candidates = [
        d for d in glob.glob(os.path.join(base, "*", "events")) if os.path.isdir(d)
    ]
    return max(candidates, key=os.path.getmtime) if candidates else None


def event_files():
    directory = events_dir()
    if not directory:
        return []
    return sorted(glob.glob(os.path.join(directory, "event-*.json")))


def load(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def is_user_message(path):
    event = load(path) or {}
    return event.get("kind") == "MessageEvent" and event.get("source") == "user"


def window():
    """The events belonging to the turn that just ended.

    The prompt hook runs *after* the runtime persists the user's MessageEvent:
    persistence is the first callback in the chain and the hooks are appended after
    it. So the mark sits one event past the message that started the turn, and
    without stepping back over it the turn reads as if the user had said nothing -
    which also hides the trigger keyword the staging below looks for.
    """
    files = event_files()
    mark = read_mark()
    if mark is None:
        return files[-TAIL_EVENTS:]
    start = mark
    if 0 < start <= len(files) and is_user_message(files[start - 1]):
        start -= 1
    return files[start:]


def read_mark():
    try:
        return int((state_dir() / MARK_NAME).read_text().strip())
    except (OSError, ValueError):
        return None


def write_mark(count):
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        (state_dir() / MARK_NAME).write_text(str(count))
    except OSError:
        pass


def part_text(parts):
    """Text out of a content-part list, e.g. `llm_message.content` or `thought`."""
    if isinstance(parts, dict):
        parts = parts.get("content")
    if not isinstance(parts, list):
        return ""
    return "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and part.get("type") == "text"
    )


def message_text(message):
    return part_text((message or {}).get("content"))


def action_line(event):
    """One line for a tool call, adding the runtime's own summary when it has one."""
    action = event.get("action") or {}
    detail = ""
    for field in ACTION_FIELDS:
        value = action.get(field)
        if isinstance(value, str) and value.strip():
            detail = " ".join(value.split())[:200]
            break
    line = f"- `{event.get('tool_name')}` {detail}".rstrip()
    summary = " ".join(str(event.get("summary") or "").split())
    return f"{line}  # {summary}" if summary else line


def summarise(event):
    """One entry for the turn, or None for events the agents do not need."""
    source = event.get("source", "?")
    kind = event.get("kind")
    if kind == "MessageEvent":
        body = message_text(event.get("llm_message")).strip()
        return f"**{source}**: {body[:MAX_ENTRY_CHARS]}" if body else None
    if kind == "ActionEvent":
        said = part_text(event.get("thought")).strip()
        lines = []
        if said:
            lines.append(f"**{source}**: {said[:MAX_ENTRY_CHARS]}")
        lines.append(action_line(event))
        return "\n".join(lines)
    return None


def fit(blocks, budget):
    """Join blocks, eliding from the middle so both ends survive.

    Cutting the tail would drop the closing summary of the turn, which is usually
    the most informative part of it; the opening user message matters too.
    """
    kept = list(blocks)
    elided = 0
    while len(kept) > 2 and sum(len(block) + 2 for block in kept) > budget:
        del kept[len(kept) // 2]
        elided += 1
    if elided:
        kept.insert(len(kept) // 2, f"_(...{elided} entries elided...)_")
    return "\n\n".join(kept)


def transcript(files):
    blocks = [block for block in (summarise(load(p) or {}) for p in files) if block]
    return fit(blocks, MAX_TOTAL_CHARS) or "(nothing recorded for this turn)"


def turn_response(files):
    """Everything the agent produced this turn, for a forced save.

    A forced save carries the whole response rather than the last message: the
    closing MessageEvent is often just "Done.", while the substance - what was
    found, what was decided - sits in the narration and the tool calls before it.
    """
    blocks = []
    for path in files:
        event = load(path) or {}
        if event.get("source") != "agent":
            continue
        block = summarise(event)
        if block:
            blocks.append(block)
    return fit(blocks, FORCE_MAX_CHARS) or "(the turn produced no agent output)"


def user_texts(files):
    texts = []
    for path in files:
        event = load(path) or {}
        if event.get("kind") == "MessageEvent" and event.get("source") == "user":
            texts.append(message_text(event.get("llm_message")))
    return texts


def parse_token(text, token):
    """(tier, rest of the line) for `token [global|project] payload`.

    Bounded to the one line the token was typed on: the runtime appends the
    triggered skill's text to the same message, and that must not be read as the
    payload.
    """
    match = re.search(re.escape(token) + r"[ \t]*([^\n]*)", text)
    if not match:
        return None
    rest = match.group(1).strip()
    tier = "project"
    head, _, tail = rest.partition(" ")
    if head.lower().rstrip(":") in ("global", "project"):
        tier, rest = head.lower().rstrip(":"), tail.strip()
    return tier, rest


def explicit_request(files):
    """(tier, payload) if the user invoked the remember command this turn."""
    found = None
    for text in user_texts(files):
        if COMMAND_TOKEN in text:
            found = parse_token(text, COMMAND_TOKEN)
    if not found:
        return None
    tier, payload = found
    payload = payload or "(no fact was given - take it from the turn)"
    return tier, payload[:EXPLICIT_MAX_CHARS]


def force_token_request(files):
    """(tier, response) when the user typed the force token this turn.

    Typed in a message, so unlike the file flag it belongs to this turn and can
    capture the response to the very message it was typed in.
    """
    tier = None
    for text in user_texts(files):
        if FORCE_TOKEN in text:
            tier = parse_token(text, FORCE_TOKEN)[0]
    if tier is None:
        return None
    return tier, turn_response(files)


def force_request(files):
    """(tier, response) when the debug force-save flag is armed for this turn.

    One shot: the flag is removed here whether or not the turn produced anything.
    """
    ready = state_dir() / FORCE_READY_NAME
    if not ready.is_file():
        return None
    try:
        tier = ready.read_text().strip().lower()
    except OSError:
        tier = ""
    if tier not in ("global", "project"):
        tier = "project"
    try:
        ready.unlink()
    except OSError:
        pass
    return tier, turn_response(files)


def stage(heading, text):
    """Append a raw, unedited note for the remember agent."""
    path = staged_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"## staged: {heading}\n\n{text}\n\n")


def stage_requests(files):
    """Stage what is already decided. Returns labels for the decide agent."""
    staged = []

    forced = force_token_request(files)
    if forced:
        stage(f"force-save (from chat, tier: {forced[0]})", forced[1])
        staged.append("force-save")
    else:
        # The file flag is for arming outside a message, where there is no turn to
        # attach to; a token typed in the message wins because it is unambiguous.
        armed = force_request(files)
        if armed:
            stage(f"force-save (debug flag, tier: {armed[0]})", armed[1])
            staged.append("force-save")

    explicit = explicit_request(files)
    if explicit:
        stage(f"user requested (tier: {explicit[0]})", explicit[1])
        staged.append("user requested")

    return staged


def staged_note(staged):
    return "\n".join(
        [
            "## Already staged - nothing to do about it",
            "",
            "A command hook already wrote this to the staging file for this turn: "
            + ", ".join(staged)
            + ".",
            "Do not stage it again, and do not restate it.",
            "Stage something further only if the turn holds a clearly durable fact"
            " that the staged text does not cover; otherwise finish immediately.",
        ]
    )


def files_section(global_memory, project_memory):
    return "\n".join(
        [
            f"- staging file, append verbatim notes here: `{staged_path()}`",
            f"- do not edit (the remember agent owns it): `{global_memory}`",
            f"- do not edit (the remember agent owns it): `{project_memory}`",
        ]
    )


def memory_section(label, path):
    try:
        body = Path(path).read_text().rstrip()
    except OSError:
        body = ""
    return f"### {label}\n`{path}`\n\n{body or '(empty)'}\n"


def main():
    args = sys.argv[1:]

    if "--arm" in args:
        args.remove("--arm")
        tier = "project"
        if args and args[0].lower() in ("global", "project"):
            tier = args[0].lower()
        state_dir().mkdir(parents=True, exist_ok=True)
        (state_dir() / FORCE_PENDING_NAME).write_text(tier)
        return

    if "--disarm" in args:
        for name in (FORCE_PENDING_NAME, FORCE_READY_NAME):
            try:
                (state_dir() / name).unlink()
            except OSError:
                pass
        return

    if "--mark" in args:
        write_mark(len(event_files()))
        return

    global_memory, project_memory = sys.argv[2], sys.argv[3]
    files = window()
    staged = stage_requests(files)

    parts = [(TEXT_DIR / "decide.md").read_text().strip(), "---"]
    if staged:
        parts += [staged_note(staged), "---"]
    parts += [
        "## This turn",
        transcript(files),
        "---",
        "## Files",
        files_section(global_memory, project_memory),
        "---",
        "## Current memory",
        memory_section("global", global_memory),
        memory_section("project", project_memory),
    ]

    state_dir().mkdir(parents=True, exist_ok=True)
    (state_dir() / DIGEST_NAME).write_text("\n\n".join(parts) + "\n")
    write_mark(len(event_files()))


if __name__ == "__main__":
    main()
