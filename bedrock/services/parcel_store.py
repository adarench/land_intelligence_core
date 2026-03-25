"""Persistent storage for normalized parcels."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:  # Prefer canonical package paths when available.
    from bedrock.contracts.base import EngineMetadata
    from bedrock.contracts.parcel import Parcel
    from bedrock.utils.geometry_normalization import infer_geometry_crs
except ImportError:  # Compatibility mode for legacy path bootstraps.
    from contracts.base import EngineMetadata
    from contracts.parcel import Parcel
    from utils.geometry_normalization import infer_geometry_crs


class ParcelStore:
    """SQLite-backed parcel persistence."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path(__file__).resolve().parents[1] / "data" / "parcels.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_parcel(self, parcel: Parcel) -> Parcel:
        existing = self.get_parcel(parcel.parcel_id)
        if existing is not None:
            if self._equivalent(existing, parcel):
                return existing
            raise ValueError(f"Parcel already exists with different data: {parcel.parcel_id}")

        with sqlite3.connect(self.db_path) as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO parcels (
                        id,
                        parcel_id,
                        geometry_json,
                        crs,
                        area_sqft,
                        centroid_x,
                        centroid_y,
                        bbox_minx,
                        bbox_miny,
                        bbox_maxx,
                        bbox_maxy,
                        jurisdiction,
                        zoning_district,
                        created_at
                    ) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        parcel.parcel_id,
                        json.dumps(parcel.geometry),
                        parcel.crs,
                        float(parcel.area_sqft),
                        float(parcel.centroid[0]),
                        float(parcel.centroid[1]),
                        float(parcel.bounding_box[0]),
                        float(parcel.bounding_box[1]),
                        float(parcel.bounding_box[2]),
                        float(parcel.bounding_box[3]),
                        parcel.jurisdiction,
                        parcel.zoning_district,
                        self._metadata_observed_at(parcel),
                    ),
                )
                row_id = int(cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO parcels_rtree (
                        id,
                        min_x,
                        max_x,
                        min_y,
                        max_y
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        float(parcel.bounding_box[0]),
                        float(parcel.bounding_box[2]),
                        float(parcel.bounding_box[1]),
                        float(parcel.bounding_box[3]),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Parcel already exists with different data: {parcel.parcel_id}") from exc
        persisted = self.get_parcel(parcel.parcel_id)
        if persisted is None:
            raise RuntimeError(f"Parcel insert succeeded but record was not retrievable: {parcel.parcel_id}")
        return persisted

    def get_parcel(self, parcel_id: str) -> Parcel | None:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT
                    id,
                    parcel_id,
                    geometry_json,
                    crs,
                    area_sqft,
                    centroid_x,
                    centroid_y,
                    bbox_minx,
                    bbox_miny,
                    bbox_maxx,
                    bbox_maxy,
                    jurisdiction,
                    zoning_district,
                    created_at
                FROM parcels
                WHERE parcel_id = ?
                """,
                (parcel_id,),
            ).fetchone()

        if row is None:
            return None

        geometry = json.loads(row["geometry_json"])
        return Parcel(
            parcel_id=row["parcel_id"],
            geometry=geometry,
            crs=self._row_crs(row["crs"], geometry),
            area_sqft=float(row["area_sqft"]),
            centroid=[float(row["centroid_x"]), float(row["centroid_y"])],
            bounding_box=[
                float(row["bbox_minx"]),
                float(row["bbox_miny"]),
                float(row["bbox_maxx"]),
                float(row["bbox_maxy"]),
            ],
            jurisdiction=row["jurisdiction"],
            zoning_district=row["zoning_district"],
            metadata=EngineMetadata(
                source_engine="bedrock.parcel_store",
                source_run_id=None,
                observed_at=self._parse_created_at(row["created_at"]),
            ),
        )

    def parcel_exists(self, parcel_id: str) -> bool:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM parcels WHERE parcel_id = ?",
                (parcel_id,),
            ).fetchone()
        return row is not None

    def replace_parcel(self, parcel: Parcel) -> Parcel:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE parcels
                SET geometry_json = ?,
                    crs = ?,
                    area_sqft = ?,
                    centroid_x = ?,
                    centroid_y = ?,
                    bbox_minx = ?,
                    bbox_miny = ?,
                    bbox_maxx = ?,
                    bbox_maxy = ?,
                    jurisdiction = ?,
                    zoning_district = ?,
                    created_at = ?
                WHERE parcel_id = ?
                """,
                (
                    json.dumps(parcel.geometry),
                    parcel.crs,
                    float(parcel.area_sqft),
                    float(parcel.centroid[0]),
                    float(parcel.centroid[1]),
                    float(parcel.bounding_box[0]),
                    float(parcel.bounding_box[1]),
                    float(parcel.bounding_box[2]),
                    float(parcel.bounding_box[3]),
                    parcel.jurisdiction,
                    parcel.zoning_district,
                    self._metadata_observed_at(parcel),
                    parcel.parcel_id,
                ),
            )
            connection.execute(
                """
                UPDATE parcels_rtree
                SET min_x = ?, max_x = ?, min_y = ?, max_y = ?
                WHERE id = (SELECT id FROM parcels WHERE parcel_id = ?)
                """,
                (
                    float(parcel.bounding_box[0]),
                    float(parcel.bounding_box[2]),
                    float(parcel.bounding_box[1]),
                    float(parcel.bounding_box[3]),
                    parcel.parcel_id,
                ),
            )
        persisted = self.get_parcel(parcel.parcel_id)
        if persisted is None:
            raise RuntimeError(f"Parcel update succeeded but record was not retrievable: {parcel.parcel_id}")
        return persisted

    def search_by_bbox(self, min_x: float, min_y: float, max_x: float, max_y: float) -> list[Parcel]:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT
                    p.parcel_id,
                    p.geometry_json,
                    p.crs,
                    p.area_sqft,
                    p.centroid_x,
                    p.centroid_y,
                    p.bbox_minx,
                    p.bbox_miny,
                    p.bbox_maxx,
                    p.bbox_maxy,
                    p.jurisdiction,
                    p.zoning_district,
                    p.created_at
                FROM parcels AS p
                JOIN parcels_rtree AS r ON r.id = p.id
                WHERE r.min_x <= ?
                  AND r.max_x >= ?
                  AND r.min_y <= ?
                  AND r.max_y >= ?
                ORDER BY p.parcel_id ASC
                """,
                (max_x, min_x, max_y, min_y),
            ).fetchall()
        return [self._row_to_parcel(row) for row in rows]

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(parcels)").fetchall()
            }
            if columns and "id" not in columns:
                self._migrate_legacy_schema(connection)

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS parcels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parcel_id TEXT NOT NULL UNIQUE,
                    geometry_json TEXT NOT NULL,
                    crs TEXT NOT NULL DEFAULT 'EPSG:4326',
                    area_sqft REAL NOT NULL,
                    centroid_x REAL NOT NULL,
                    centroid_y REAL NOT NULL,
                    bbox_minx REAL NOT NULL,
                    bbox_miny REAL NOT NULL,
                    bbox_maxx REAL NOT NULL,
                    bbox_maxy REAL NOT NULL,
                    jurisdiction TEXT NOT NULL,
                    zoning_district TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(parcels)").fetchall()
            }
            if "zoning_district" not in columns:
                connection.execute("ALTER TABLE parcels ADD COLUMN zoning_district TEXT")
            if "crs" not in columns:
                connection.execute("ALTER TABLE parcels ADD COLUMN crs TEXT NOT NULL DEFAULT 'EPSG:4326'")
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS parcels_rtree USING rtree(
                    id,
                    min_x,
                    max_x,
                    min_y,
                    max_y
                )
                """
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO parcels_rtree (id, min_x, max_x, min_y, max_y)
                SELECT id, bbox_minx, bbox_maxx, bbox_miny, bbox_maxy
                FROM parcels
                """
            )

    def _migrate_legacy_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute("ALTER TABLE parcels RENAME TO parcels_legacy")
        connection.execute(
            """
            CREATE TABLE parcels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parcel_id TEXT NOT NULL UNIQUE,
                geometry_json TEXT NOT NULL,
                crs TEXT NOT NULL DEFAULT 'EPSG:4326',
                area_sqft REAL NOT NULL,
                centroid_x REAL NOT NULL,
                centroid_y REAL NOT NULL,
                bbox_minx REAL NOT NULL,
                bbox_miny REAL NOT NULL,
                bbox_maxx REAL NOT NULL,
                bbox_maxy REAL NOT NULL,
                jurisdiction TEXT NOT NULL,
                zoning_district TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            INSERT INTO parcels (
                parcel_id,
                geometry_json,
                crs,
                area_sqft,
                centroid_x,
                centroid_y,
                bbox_minx,
                bbox_miny,
                bbox_maxx,
                bbox_maxy,
                jurisdiction,
                zoning_district,
                created_at
            )
            SELECT
                parcel_id,
                geometry_json,
                'EPSG:4326',
                area_sqft,
                centroid_x,
                centroid_y,
                bbox_minx,
                bbox_miny,
                bbox_maxx,
                bbox_maxy,
                jurisdiction,
                NULL,
                created_at
            FROM parcels_legacy
            """
        )
        connection.execute("DROP TABLE parcels_legacy")

    def _row_to_parcel(self, row: sqlite3.Row) -> Parcel:
        geometry = json.loads(row["geometry_json"])
        return Parcel(
            parcel_id=row["parcel_id"],
            geometry=geometry,
            crs=self._row_crs(row["crs"], geometry),
            area_sqft=float(row["area_sqft"]),
            centroid=[float(row["centroid_x"]), float(row["centroid_y"])],
            bounding_box=[
                float(row["bbox_minx"]),
                float(row["bbox_miny"]),
                float(row["bbox_maxx"]),
                float(row["bbox_maxy"]),
            ],
            jurisdiction=row["jurisdiction"],
            zoning_district=row["zoning_district"],
            metadata=EngineMetadata(
                source_engine="bedrock.parcel_store",
                source_run_id=None,
                observed_at=self._parse_created_at(row["created_at"]),
            ),
        )

    @staticmethod
    def _equivalent(left: Parcel, right: Parcel) -> bool:
        return (
            left.parcel_id == right.parcel_id
            and left.geometry == right.geometry
            and left.crs == right.crs
            and float(left.area_sqft) == float(right.area_sqft)
            and list(left.centroid or []) == list(right.centroid or [])
            and list(left.bounding_box or []) == list(right.bounding_box or [])
            and left.jurisdiction == right.jurisdiction
            and left.zoning_district == right.zoning_district
        )

    @staticmethod
    def _metadata_observed_at(parcel: Parcel) -> str:
        if parcel.metadata is not None and parcel.metadata.observed_at is not None:
            return parcel.metadata.observed_at.astimezone(timezone.utc).isoformat()
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_created_at(value: object) -> datetime:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        raise TypeError(f"Unsupported created_at value: {value!r}")

    @staticmethod
    def _row_crs(stored_crs: object, geometry: dict) -> str:
        inferred = infer_geometry_crs(geometry)
        normalized = str(stored_crs).strip() if stored_crs is not None else ""
        if not normalized or normalized != inferred:
            return inferred
        return normalized
