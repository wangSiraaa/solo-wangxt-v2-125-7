"""命名情景快照 API 行为核对。

验收对应：
* 保存/列表/读取/重命名/删除全流程；
* 快照只含输入，不含任何网格计算结果；
* 删除快照不影响源/气象记录；
* 引用已不存在的源时明确标注失效（前端据此给出可理解提示，
  而不是静默改用别的源）；
* 内存回退模式显式声明"本次运行有效"的保存语义。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _payload(source_id=1, met_id=1, wind_from=270.0, nx=121):
    return {
        "source_id": source_id,
        "met_id": met_id,
        "source": {
            "name": "虚构·华北示范热电厂 #1", "lon": 116.40, "lat": 39.90,
            "stack_height_m": 120.0, "emission_rate_g_s": 50.0,
            "stack_diameter_m": 4.0, "exit_velocity_ms": 18.0,
            "stack_temp_k": 410.0, "pollutant": "SO2",
        },
        "meteorology": {
            "name": "界面情景", "wind_from_deg": wind_from,
            "wind_speed_ms": 6.0, "stability_class": "D",
            "ambient_temp_k": 293.15, "pressure_hpa": 1013.0,
            "background_conc_ug_m3": 15.0,
        },
        "grid": {
            "downwind_extent_m": 6000.0, "crosswind_extent_m": 2000.0,
            "upwind_extent_m": 300.0, "nx": nx, "ny": 81,
        },
        "plume_rise": {"use_plume_rise": False},
        "parameterization": "briggs_rural",
        "power_law": None,
        "calm_threshold_ms": 1.0,
    }


def _create(name="课堂演示快照", **payload_kw) -> dict:
    resp = client.post(
        "/api/snapshots", json={"name": name, "payload": _payload(**payload_kw)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _cleanup(snapshot_id: int) -> None:
    client.delete(f"/api/snapshots/{snapshot_id}")


def test_snapshot_crud_roundtrip():
    created = _create(wind_from=45.0, nx=61)
    sid = created["id"]
    try:
        assert created["name"] == "课堂演示快照"
        assert created["source_exists"] is True
        assert created["met_exists"] is True

        # 列表中可见，且带持久化语义说明
        listing = client.get("/api/snapshots").json()
        assert "persistence" in listing
        assert any(s["id"] == sid for s in listing["snapshots"])

        # 读取：payload 原样返回（有效输入值）
        got = client.get(f"/api/snapshots/{sid}").json()
        assert got["payload"]["meteorology"]["wind_from_deg"] == 45.0
        assert got["payload"]["grid"]["nx"] == 61
        assert got["payload"]["source_id"] == 1

        # 重命名
        ren = client.patch(f"/api/snapshots/{sid}", json={"name": "第 7 周演示"})
        assert ren.status_code == 200
        assert ren.json()["name"] == "第 7 周演示"
        assert client.get(f"/api/snapshots/{sid}").json()["name"] == "第 7 周演示"
    finally:
        _cleanup(sid)
    assert client.get(f"/api/snapshots/{sid}").status_code == 404


def test_snapshot_stores_inputs_not_results():
    """快照 payload 只含输入键，绝不保存网格浓度等计算结果。"""
    created = _create()
    sid = created["id"]
    try:
        payload = created["payload"]
        assert set(payload.keys()) == {
            "source_id", "met_id", "source", "meteorology", "grid",
            "plume_rise", "parameterization", "power_law", "calm_threshold_ms",
        }
        forbidden = {
            "plume_field_ug_m3", "total_conc_ug_m3", "iso_levels_ug_m3",
            "lon_grid", "lat_grid", "diagnostics",
        }
        assert forbidden.isdisjoint(payload.keys())
        assert forbidden.isdisjoint(payload["grid"].keys())
    finally:
        _cleanup(sid)


def test_delete_snapshot_keeps_sources_and_meteorology():
    sources_before = client.get("/api/sources").json()
    mets_before = client.get("/api/meteorology").json()
    created = _create()
    sid = created["id"]
    assert client.delete(f"/api/snapshots/{sid}").status_code == 200
    assert client.get("/api/sources").json() == sources_before
    assert client.get("/api/meteorology").json() == mets_before
    # 重复删除 -> 404
    assert client.delete(f"/api/snapshots/{sid}").status_code == 404


def test_missing_reference_flagged_not_silent():
    """引用不存在的源/气象：接口明确标注失效，由前端给出可理解提示。"""
    created = _create(source_id=9999, met_id=8888)
    sid = created["id"]
    try:
        assert created["source_exists"] is False
        assert created["met_exists"] is False
        got = client.get(f"/api/snapshots/{sid}").json()
        assert got["source_exists"] is False
        assert got["met_exists"] is False
        # 引用本身保留，便于提示"引用的源 #9999 已不存在"
        assert got["source_id"] == 9999
        assert got["met_id"] == 8888
    finally:
        _cleanup(sid)


def test_memory_backend_persistence_semantics_explicit():
    """内存回退模式下，快照语义必须是"本次运行有效"并显式告知。"""
    health = client.get("/api/health").json()
    listing = client.get("/api/snapshots").json()
    assert listing["persistence"]
    if health["repository"] == "memory":
        assert "本次运行" in listing["persistence"]
        assert "memory" in listing["persistence"]
    else:
        assert "scenario_snapshot" in listing["persistence"]


def test_snapshot_name_validation():
    resp = client.post("/api/snapshots", json={"name": "", "payload": _payload()})
    assert resp.status_code == 422
    created = _create()
    sid = created["id"]
    try:
        assert (
            client.patch(f"/api/snapshots/{sid}", json={"name": ""}).status_code
            == 422
        )
    finally:
        _cleanup(sid)
