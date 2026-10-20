"""Print the agent profile id for this conversation, else 'default'.

Reads OH_RUNTIME_LAUNCHED_PROFILE, which the runtime sets to a JSON blob holding
agent_profile_id. The profile *name* is not exposed inside the container, which is
why memory folders are keyed by id (or by PROJECT_MEMORY_KEY when set).
"""
import json
import os

profile = json.loads(os.environ.get("OH_RUNTIME_LAUNCHED_PROFILE") or "{}")
print(profile.get("agent_profile_id") or "default")
