"""JSON file-backed decision persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from bedrock.contracts.decision import DecisionRecord

_DEFAULT_DECISIONS_DIR = Path(__file__).resolve().parents[1] / "decisions"


class DecisionStore:
    def __init__(self, decisions_dir: Optional[Path] = None) -> None:
        self._dir = decisions_dir or _DEFAULT_DECISIONS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, record: DecisionRecord) -> Path:
        path = self._dir / f"{record.decision_id}.json"
        path.write_text(
            json.dumps(record.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )
        return path

    def load(self, decision_id: str) -> DecisionRecord:
        path = self._dir / f"{decision_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Decision '{decision_id}' not found")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return DecisionRecord.model_validate(payload)

    def list_decisions(
        self,
        *,
        parcel_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[DecisionRecord]:
        records: list[DecisionRecord] = []
        for path in sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                record = DecisionRecord.model_validate(payload)
            except Exception:
                continue
            if parcel_id and record.parcel_id != parcel_id:
                continue
            if status and record.status != status:
                continue
            records.append(record)

        if offset:
            records = records[offset:]
        if limit:
            records = records[:limit]
        return records

    def update(self, decision_id: str, **fields: object) -> DecisionRecord:
        record = self.load(decision_id)
        data = record.model_dump(mode="json")
        for key, value in fields.items():
            if key in data and value is not None:
                data[key] = value
        updated = DecisionRecord.model_validate(data)
        self.save(updated)
        return updated
