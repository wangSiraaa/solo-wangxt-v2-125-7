"""数据仓储：PostgreSQL/PostGIS 优先，缺省回退内存虚构数据。

表结构见 db/init.sql（PostGIS，geometry(Point,4326)，带 GIST 索引）。
运行时每个请求短连接；DATABASE_URL 未设置或连接失败时，
透明回退到 seed_data 内存数据，并在 /api/health 标明后端类型。

命名情景快照（scenario_snapshot）与源/气象原始记录分开持久化：
快照只保存一次课堂演示的输入（有效值 + 对源/气象记录的引用），
不保存任何网格计算结果；删除快照不影响源/气象记录，反之亦然。
内存回退模式下快照仅存活于后端进程内（本次运行有效，重启即丢失），
该语义通过 snapshot_persistence 显式暴露给前端。
"""
from __future__ import annotations

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


_SNAPSHOT_COLS = ("id", "name", "created_at", "updated_at", "payload")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryRepository:
    backend = "memory"
    snapshot_persistence = (
        "memory：快照仅保存在后端进程内存中，本次运行内有效，服务重启后丢失"
    )

    def __init__(self) -> None:
        self.sources = [dict(s) for s in SEED_SOURCES]
        self.meteorology = [dict(m) for m in SEED_METEOROLOGY]
        self.snapshots: list[dict] = []
        self._snapshot_next_id = 1

    def list_sources(self) -> list[dict]:
        return [dict(s) for s in self.sources]

    def get_source(self, source_id: int) -> dict | None:
        return next((dict(s) for s in self.sources if s["id"] == source_id), None)

    def list_meteorology(self) -> list[dict]:
        return [dict(m) for m in self.meteorology]

    def get_meteorology(self, met_id: int) -> dict | None:
        return next((dict(m) for m in self.meteorology if m["id"] == met_id), None)

    # ---- 情景快照（进程内存，本次运行有效） ----

    def list_snapshots(self) -> list[dict]:
        return [dict(s) for s in self.snapshots]

    def create_snapshot(self, name: str, payload: dict) -> dict:
        now = _utcnow_iso()
        snap = {
            "id": self._snapshot_next_id,
            "name": name,
            "created_at": now,
            "updated_at": now,
            "payload": payload,
        }
        self._snapshot_next_id += 1
        self.snapshots.append(snap)
        return dict(snap)

    def get_snapshot(self, snapshot_id: int) -> dict | None:
        return next(
            (dict(s) for s in self.snapshots if s["id"] == snapshot_id), None
        )

    def rename_snapshot(self, snapshot_id: int, name: str) -> dict | None:
        for s in self.snapshots:
            if s["id"] == snapshot_id:
                s["name"] = name
                s["updated_at"] = _utcnow_iso()
                return dict(s)
        return None

    def delete_snapshot(self, snapshot_id: int) -> bool:
        before = len(self.snapshots)
        self.snapshots = [s for s in self.snapshots if s["id"] != snapshot_id]
        return len(self.snapshots) < before


class PostgisRepository:
    backend = "postgis"
    snapshot_persistence = (
        "postgis：快照持久化于 scenario_snapshot 表，"
        "与排放源/气象原始记录分表存储，互不影响"
    )

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

    # ---- 情景快照（scenario_snapshot 表，与源/气象记录分表） ----

    @staticmethod
    def _snapshot_row(cur) -> list[dict[str, Any]]:
        rows = []
        for row in cur.fetchall():
            d = dict(zip(_SNAPSHOT_COLS, row))
            # JSONB 由 psycopg 直接解码为 dict；时间戳统一为 ISO 字符串
            d["created_at"] = d["created_at"].isoformat()
            d["updated_at"] = d["updated_at"].isoformat()
            rows.append(d)
        return rows

    def list_snapshots(self) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, created_at, updated_at, payload
                FROM scenario_snapshot ORDER BY id
                """
            )
            return self._snapshot_row(cur)

    def create_snapshot(self, name: str, payload: dict) -> dict:
        from psycopg.types.json import Jsonb

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scenario_snapshot (name, payload)
                VALUES (%s, %s)
                RETURNING id, name, created_at, updated_at, payload
                """,
                (name, Jsonb(payload)),
            )
            return self._snapshot_row(cur)[0]

    def get_snapshot(self, snapshot_id: int) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, created_at, updated_at, payload
                FROM scenario_snapshot WHERE id = %s
                """,
                (snapshot_id,),
            )
            rows = self._snapshot_row(cur)
            return rows[0] if rows else None

    def rename_snapshot(self, snapshot_id: int, name: str) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scenario_snapshot
                SET name = %s, updated_at = now()
                WHERE id = %s
                RETURNING id, name, created_at, updated_at, payload
                """,
                (name, snapshot_id),
            )
            rows = self._snapshot_row(cur)
            return rows[0] if rows else None

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
            _repo = candidate
        except Exception as exc:  # 连接/扩展不可用 -> 回退
            print(f"[repository] PostGIS 不可用，回退内存仓储: {exc}")
            _repo = MemoryRepository()
    else:
        _repo = MemoryRepository()
    return _repo
