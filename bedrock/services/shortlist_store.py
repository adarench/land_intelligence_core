"""JSON file-backed shortlist persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DEFAULT_SHORTLIST_PATH = Path(__file__).resolve().parents[1] / "data" / "shortlist.json"


class ShortlistStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or _DEFAULT_SHORTLIST_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("[]", encoding="utf-8")

    def _load(self) -> list[dict]:
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _save(self, items: list[dict]) -> None:
        self._path.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")

    def list_items(self) -> list[dict]:
        return self._load()

    def add(self, parcel_id: str) -> dict:
        items = self._load()
        existing = next((i for i in items if i["parcel_id"] == parcel_id), None)
        if existing:
            return existing
        entry = {
            "parcel_id": parcel_id,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        items.append(entry)
        self._save(items)
        return entry

    def remove(self, parcel_id: str) -> bool:
        items = self._load()
        filtered = [i for i in items if i["parcel_id"] != parcel_id]
        if len(filtered) == len(items):
            return False
        self._save(filtered)
        return True

    def clear(self) -> int:
        items = self._load()
        count = len(items)
        self._save([])
        return count

    def contains(self, parcel_id: str) -> bool:
        return any(i["parcel_id"] == parcel_id for i in self._load())
