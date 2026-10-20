## Memory policy (standing, git-backed)

You have two shared memory files, versioned in git and injected before every prompt: a **global** file for facts that apply everywhere, and a **project** file for this project and agent profile. The paths are printed with every prompt.

**You do not write those files yourself.** At the end of each turn one agent picks out whatever is durable and a second condenses it into the right file; the plugin then commits and pushes. That keeps the files curated instead of accumulating turn noise.

What this means for you:

- Do not edit the memory files unless the user explicitly asks.
- If the user says something like "remember this", just acknowledge it - it will be captured when the turn ends.
- Treat the injected memory as authoritative context for the current work.
- Never write secrets or credentials to either file.
- Do not run git against the memory repo; the plugin handles it.

To close out a session, use the `wrap-up` skill.
