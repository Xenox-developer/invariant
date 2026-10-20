"""Project-scoped metadata; native Codex owns the conversation history."""
import json
import math
import os
from pathlib import Path
import re
import stat

CHAT_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
MODEL = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
EFFORT = r"[a-z][a-z0-9_-]{0,31}"


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(CHAT_ID, value) is not None


class ChatStore:
    def __init__(self, state, project):
        self.state, self.project = state, str(project)

    def _read(self, path):
        try:
            with open(path, "r", opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW | os.O_NONBLOCK)) as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 4096:
                    raise ValueError()
                return json.load(source)
        except (OSError, ValueError):
            raise RuntimeError("Saved chat metadata is missing or invalid: " + str(path)) from None

    def _validate(self, value):
        if (not isinstance(value, dict) or value.get("schema") != 1
                or value.get("project") != self.project or not valid_id(value.get("id"))
                or not isinstance(value.get("model"), str) or not re.fullmatch(MODEL, value["model"])
                or (value.get("effort") is not None and
                    (not isinstance(value["effort"], str) or not re.fullmatch(EFFORT, value["effort"])))
                or any(type(value.get(key)) not in (float, int) or not math.isfinite(value[key])
                       for key in ("created_at", "updated_at"))):
            raise RuntimeError("Saved chat does not belong to this project or has invalid metadata")
        return value

    def load(self, chat_id=None):
        if (self.state / "chats").is_symlink():
            raise RuntimeError("Unsafe chat metadata directory")
        if chat_id is None:
            pointer = self.state / "latest-chat.json"
            if not pointer.exists():
                raise RuntimeError("No saved chat in this project. Start one with invariant run")
            value = self._read(pointer)
            chat_id = value.get("chat") if isinstance(value, dict) else None
        if not valid_id(chat_id):
            raise RuntimeError("Invalid chat ID; use invariant chats to find a saved chat")
        value = self._validate(self._read(self.state / "chats" / (chat_id + ".json")))
        if value["id"] != chat_id:
            raise RuntimeError("Saved chat ID does not match its metadata")
        return value

    def save(self, value):
        from .workspace import private_write
        self._validate(value)
        directory = self.state / "chats"
        if directory.is_symlink():
            raise RuntimeError("Unsafe chat metadata directory")
        private_write(directory / (value["id"] + ".json"), value)
        private_write(self.state / "latest-chat.json", {"chat": value["id"]})

    def list(self):
        directory = self.state / "chats"
        if directory.is_symlink():
            raise RuntimeError("Unsafe chat metadata directory")
        return sorted((self.load(path.stem) for path in directory.glob("*.json")),
                      key=lambda row: (row["updated_at"], row["id"]), reverse=True)


def list_chats(*, machine=False):
    from .workspace import load_project, state_for
    from datetime import datetime
    project = Path.cwd().resolve()
    load_project(project)
    rows = ChatStore(state_for(project), project).list()
    if machine:
        print(json.dumps(rows))
    elif not rows:
        print("No saved chats in this project. Start one with invariant run")
    else:
        print("Saved chats in " + str(project) + ":")
        for row in rows:
            print(row["id"], row["model"], "effort=" + (row["effort"] or "default"),
                  datetime.fromtimestamp(row["updated_at"]).astimezone().isoformat(timespec="seconds"))
        print("Continue latest: invariant resume · Choose one: invariant resume CHAT_ID")
