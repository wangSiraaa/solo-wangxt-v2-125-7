"""数据仓储：PostgreSQL/PostGIS 优先，缺省回退内存虚构数据。

表结构见 db/init.sql（PostGIS，geometry(Point,4326)，带 GIST 索引）。
运行时每个请求短连接；DATABASE_URL 未设置或连接失败时，
透明回退到 seed_data 内存数据，并在 /api/health 标明后端类型。

命名情景快照（scenario_snapshot）与源/气象原始记录分开持久化：
* PostGIS 模式：写入独立的 scenario_snapshot 表（JSONB 载荷），跨重启保留；
* 内存回退模式：快照只保存在**本次运行**的进程内存中，
  重启即清空（snapshot_store = "memory_session"），接口会显式标明该语义。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .config import settings
from .seed_data import SEED_METEOROLOGY, SEED_SOURCES

_SOURCE_COLS = (
    "id", "name", "pollutant", "lon", "lat", "stack_height_m",
    "emission_rate_g_s", "stack_diameter_m", "exit_velocity_ms", "stack_temp_k",
)
_MET_COLS = (
    "id", "name", "wind_from_deg", "wind_speed_ms", "stability_class",
    "ambient_temp_k", "pressure_hpa", "background_conc_ug_m3",
)

# 与 db/init.sql 保持一致；为已初始化的旧库兜底（幂等）
_ENSURE_SNAPSHOT_TABLE = """
CREATE TABLE IF NOT EXISTS scenario_snapshot (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryRepository:
    backend = "memory"
    # 快照语义：仅本次运行有效，进程重启即丢失（不落盘）
    snapshot_store = "memory_session"

    def __init__(self) -> None:
        self.sources = [dict(s) for s in SEED_SOURCES]
        self.meteorology = [dict(m) for m in SEED_METEOROLOGY]
        self._snapshots: list[dict] = []
        self._snapshot_seq = 0

    def list_sources(self) -> list[dict]:
        return [dict(s) for s in self.sources]

    def get_source(self, source_id: int) -> dict | None:
        return next((dict(s) for s in self.sources if s["id"] == source_id), None)

    def list_meteorology(self) -> list[dict]:
        return [dict(m) for m in self.meteorology]

    def get_meteorology(self, met_id: int) -> dict | None:
        return next((dict(m) for m in self.meteorology if m["id"] == met_id), None)

    # ---- 命名情景快照（仅本次运行） ----

    def list_snapshots(self) -> list[dict]:
        return [deepcopy(s) for s in self._snapshots]

    def create_snapshot(self, name: str, payload: dict) -> dict:
        self._snapshot_seq += 1
        now = _utcnow_iso()
        row = {
            "id": self._snapshot_seq,
            "name": name,
            "payload": deepcopy(payload),
            "created_at": now,
            "updated_at": now,
        }
        self._snapshots.append(row)
        return deepcopy(row)

    def get_snapshot(self, snapshot_id: int) -> dict | None:
        row = next((s for s in self._snapshots if s["id"] == snapshot_id), None)
        return deepcopy(row) if row else None

    def rename_snapshot(self, snapshot_id: int, name: str) -> dict | None:
        row = next((s for s in self._snapshots if s["id"] == snapshot_id), None)
        if row is None:
            return None
        row["name"] = name
        row["updated_at"] = _utcnow_iso()
        return deepcopy(row)

    def delete_snapshot(self, snapshot_id: int) -> bool:
        before = len(self._snapshots)
        self._snapshots = [s for s in self._snapshots if s["id"] != snapshot_id]
        return len(self._snapshots) != before


class PostgisRepository:
    backend = "postgis"
    # 快照语义：写入 scenario_snapshot 表，跨重启持久保留
    snapshot_store = "postgis"

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        # 由调用方确保服务就绪；失败由 get_repository 的探测拦截
        return psycopg.connect(self.database_url, connect_timeout=3)

    @staticmethod
    def _rows(cur, columns: tuple[str, ...]) -> list[dict[str, Any]]:
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def list_sources(self) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, pollutant,
                       ST_X(location) AS lon, ST_Y(location) AS lat,
                       stack_height_m, emission_rate_g_s,
                       stack_diameter_m, exit_velocity_ms, stack_temp_k
                FROM emission_source ORDER BY id
                """
            )
            return self._rows(cur, _SOURCE_COLS)

    def get_source(self, source_id: int) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, pollutant,
                       ST_X(location) AS lon, ST_Y(location) AS lat,
                       stack_height_m, emission_rate_g_s,
                       stack_diameter_m, exit_velocity_ms, stack_temp_k
                FROM emission_source WHERE id = %s
                """,
                (source_id,),
            )
            rows = self._rows(cur, _SOURCE_COLS)
            return rows[0] if rows else None

    def list_meteorology(self) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {", ".join(_MET_COLS)}
                FROM meteorology_scenario ORDER BY id
                """
            )
            return self._rows(cur, _MET_COLS)

    def get_meteorology(self, met_id: int) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {", ".join(_MET_COLS)}
                FROM meteorology_scenario WHERE id = %s
                """,
                (met_id,),
            )
            rows = self._rows(cur, _MET_COLS)
            return rows[0] if rows else None

    # ---- 命名情景快照（scenario_snapshot 表，与源/气象记录分离） ----

    @staticmethod
    def _snapshot_row(row) -> dict:
        sid, name, payload, created_at, updated_at = row
        return {
            "id": sid,
            "name": name,
            "payload": payload,
            "created_at": created_at.isoformat(),
            "updated_at": updated_at.isoformat(),
        }

    def list_snapshots(self) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, payload, created_at, updated_at
                FROM scenario_snapshot ORDER BY id
                """
            )
            return [self._snapshot_row(r) for r in cur.fetchall()]

    def create_snapshot(self, name: str, payload: dict) -> dict:
        from psycopg.types.json import Jsonb

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scenario_snapshot (name, payload)
                VALUES (%s, %s)
                RETURNING id, name, payload, created_at, updated_at
                """,
                (name, Jsonb(payload)),
            )
            return self._snapshot_row(cur.fetchone())

    def get_snapshot(self, snapshot_id: int) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, payload, created_at, updated_at
                FROM scenario_snapshot WHERE id = %s
                """,
                (snapshot_id,),
            )
            row = cur.fetchone()
            return self._snapshot_row(row) if row else None

    def rename_snapshot(self, snapshot_id: int, name: str) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scenario_snapshot
                SET name = %s, updated_at = now()
                WHERE id = %s
                RETURNING id, name, payload, created_at, updated_at
                """,
                (name, snapshot_id),
            )
            row = cur.fetchone()
            return self._snapshot_row(row) if row else None

    def delete_snapshot(self, snapshot_id: int) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM scenario_snapshot WHERE id = %s", (snapshot_id,)
            )
            return cur.rowcount > 0


_repo: MemoryRepository | PostgisRepository | None = None


def get_repository() -> MemoryRepository | PostgisRepository:
    """惰性初始化：探测 PostGIS，失败回退内存仓储。"""
    global _repo
    if _repo is not None:
        return _repo
    if settings.database_url:
        candidate = PostgisRepository(settings.database_url)
        try:
            with candidate._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT PostGIS_version()")
                cur.fetchone()
                cur.execute(_ENSURE_SNAPSHOT_TABLE)
            _repo = candidate
        except Exception as exc:  # 连接/扩展不可用 -> 回退
            print(f"[repository] PostGIS 不可用，回退内存仓储: {exc}")
            _repo = MemoryRepository()
    else:
        _repo = MemoryRepository()
    return _repo
