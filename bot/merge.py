"""Union two articles.json files by id (newest first). Used by the workflow
so two overlapping runs can never produce a git merge conflict.

    python -m bot.merge LOCAL.json REMOTE.json   # writes result into LOCAL.json
"""
import json
import sys

import config


def main(local_path: str, remote_path: str) -> None:
    def load(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    seen, out = set(), []
    for a in sorted(load(local_path) + load(remote_path), key=lambda a: a["published"], reverse=True):
        if a["id"] not in seen:
            seen.add(a["id"])
            out.append(a)
    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(out[: config.MAX_STORED_ARTICLES], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
