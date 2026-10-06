"""命名情景快照的后端行为核对。

要点：
* 快照只保存输入参数有效值，绝不保存/回放网格计算结果；
* 恢复 = 取回输入 + 重新调用当前计算接口；
* 删除快照不影响源/气象记录；
* 引用失效（源/气象已不存在）时返回可理解的 409，而非静默改用别的记录；
* 内存回退模式下快照为"本次运行"语义，并在 health/列表接口中显式标明。
"""
from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _payload(wind_from=270.0, nx=121, ny=81, source_id=1, met_id=1):
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
        "plume_rise": {"use_plume_rise": False},
        "parameterization": "briggs_rural",
        "power_law": None,
        "grid": {
            "downwind_extent_m": 6000.0, "crosswind_extent_m": 2000.0,
            "upwind_extent_m": 300.0, "nx": nx, "ny": ny,
        },
        "calm_threshold_ms": 1.0,
    }


def _create(name="课堂演示快照", **kw) -> dict:
    resp = client.post("/api/snapshots", json={"name": name, "payload": _payload(**kw)})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_snapshot_crud_roundtrip():
    created = _create("第6周·西风演示")
    assert created["name"] == "第6周·西风演示"
    assert created["payload"]["meteorology"]["wind_from_deg"] == 270.0

    listed = client.get("/api/snapshots").json()
    assert any(s["id"] == created["id"] for s in listed["snapshots"])
    # 内存回退模式下必须显式标明"本次运行"语义
    assert listed["snapshot_store"] in ("memory_session", "postgis")
    assert listed["snapshot_store_note"]

    renamed = client.patch(
        f"/api/snapshots/{created['id']}", json={"name": "第7周·继续讲解"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "第7周·继续讲解"

    got = client.get(f"/api/snapshots/{created['id']}")
    assert got.status_code == 200
    assert got.json()["payload"]["grid"]["nx"] == 121

    deleted = client.delete(f"/api/snapshots/{created['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/snapshots/{created['id']}").status_code == 404


def test_snapshot_name_validation():
    resp = client.post("/api/snapshots", json={"name": "", "payload": _payload()})
    assert resp.status_code == 422
    snap = _create("待重命名")
    assert client.patch(
        f"/api/snapshots/{snap['id']}", json={"name": ""}
    ).status_code == 422
    client.delete(f"/api/snapshots/{snap['id']}")


def test_snapshot_stores_inputs_not_results():
    """快照载荷中不得出现任何网格浓度结果字段。"""
    snap = _create("只存输入")
    body = str(snap["payload"])
    for forbidden in ("plume_field", "total_conc", "iso_levels", "lon_grid"):
        assert forbidden not in body
    restored = client.post(f"/api/snapshots/{snap['id']}/restore").json()
    assert "plume_field_ug_m3" not in restored
    assert "restore_note" in restored
    client.delete(f"/api/snapshots/{snap['id']}")


def test_restore_then_recompute_gives_original_inputs():
    """验收路径：保存(270°, nx=121) →（界面改风向/分辨率）→ 恢复 →
    用恢复的输入重新调用 /api/plume/grid，得到原风向与分辨率的结果。"""
    snap = _create("西风·粗网格", wind_from=270.0, nx=61, ny=41)
    restored = client.post(f"/api/snapshots/{snap['id']}/restore")
    assert restored.status_code == 200
    p = restored.json()["payload"]
    assert p["meteorology"]["wind_from_deg"] == 270.0
    assert p["grid"]["nx"] == 61

    # 用恢复出的输入重新调用当前计算接口（不是回放旧结果）
    req = {
        "source": p["source"],
        "meteorology": p["meteorology"],
        "grid": p["grid"],
        "plume_rise": p["plume_rise"],
        "parameterization": p["parameterization"],
        "power_law": p["power_law"],
        "calm_threshold_ms": p["calm_threshold_ms"],
    }
    grid = client.post("/api/plume/grid", json=req)
    assert grid.status_code == 200, grid.text
    data = grid.json()
    assert data["wind"]["wind_from_deg"] == 270.0
    assert data["wind"]["transport_bearing_deg"] == 90.0  # 西风 -> 向东输运
    assert np.array(data["plume_field_ug_m3"]).shape == (41, 61)
    client.delete(f"/api/snapshots/{snap['id']}")


def test_delete_snapshot_keeps_sources_and_meteorology():
    sources_before = client.get("/api/sources").json()
    mets_before = client.get("/api/meteorology").json()
    snap = _create("删我不删源")
    assert client.delete(f"/api/snapshots/{snap['id']}").status_code == 200
    assert client.get("/api/sources").json() == sources_before
    assert client.get("/api/meteorology").json() == mets_before
    assert client.get("/api/sources/1").status_code == 200
    assert client.get("/api/meteorology/1").status_code == 200


def test_restore_with_missing_source_fails_clearly():
    """引用的源已不存在：返回 409 + 可理解提示，绝不静默改用别的源。"""
    snap = _create("悬空引用", source_id=9999)
    resp = client.post(f"/api/snapshots/{snap['id']}/restore")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "stale_reference"
    assert "9999" in body["message"]
    assert "已不存在" in body["message"]
    # 快照本身保留，源列表未被改动
    assert client.get(f"/api/snapshots/{snap['id']}").status_code == 200
    assert all(s["id"] != 9999 for s in client.get("/api/sources").json())
    client.delete(f"/api/snapshots/{snap['id']}")


def test_restore_with_missing_met_fails_clearly():
    snap = _create("悬空气象", met_id=8888)
    resp = client.post(f"/api/snapshots/{snap['id']}/restore")
    assert resp.status_code == 409
    assert "气象情景" in resp.json()["message"]
    client.delete(f"/api/snapshots/{snap['id']}")


def test_restore_missing_snapshot_404():
    assert client.post("/api/snapshots/424242/restore").status_code == 404
    assert client.delete("/api/snapshots/424242").status_code == 404


def test_snapshot_store_semantics_advertised():
    """内存回退模式：health 明确说明快照只在本次运行内有效。"""
    h = client.get("/api/health").json()
    assert h["snapshot_store"] in ("memory_session", "postgis")
    if h["snapshot_store"] == "memory_session":
        assert "本次运行" in h["snapshot_store_note"]
    # 同一运行内，快照跨请求可见（进程内存语义）
    snap = _create("本次运行可见")
    listed = client.get("/api/snapshots").json()
    assert any(s["id"] == snap["id"] for s in listed["snapshots"])
    client.delete(f"/api/snapshots/{snap['id']}")
