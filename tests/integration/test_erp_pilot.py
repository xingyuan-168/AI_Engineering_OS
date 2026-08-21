from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codex_ai_os.pilots.erp import ErpPilotGenerator


def test_generated_erp_api_completes_procurement_vertical_slice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generated = ErpPilotGenerator().generate(tmp_path)
    assert "src/erp_api/app.py" in generated.created_or_updated
    assert ErpPilotGenerator().generate(tmp_path).created_or_updated == ()
    monkeypatch.setattr(sys, "path", [str(tmp_path / "src"), *sys.path])
    sys.modules.pop("erp_api", None)
    sys.modules.pop("erp_api.app", None)
    sys.modules.pop("erp_api.db", None)
    app_module = importlib.import_module("erp_api.app")
    app = app_module.create_app(tmp_path / "runtime" / "erp.db")
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    supplier = client.post(
        "/suppliers", json={"code": "SUP-001", "name": "Fixture Supplier"}
    )
    assert supplier.status_code == 201
    supplier_id = supplier.json()["id"]
    assert client.post(
        "/suppliers", json={"code": "SUP-001", "name": "Duplicate"}
    ).status_code == 409

    requisition = client.post(
        "/requisitions",
        json={
            "requester": "alice",
            "justification": "Replenish safety stock",
            "items": [
                {
                    "sku": "PART-001",
                    "description": "Industrial component",
                    "quantity": 3,
                    "unit_price_cents": 1250,
                }
            ],
        },
    )
    assert requisition.status_code == 201
    requisition_id = requisition.json()["id"]

    premature = client.post(
        "/purchase-orders",
        json={"requisition_id": requisition_id, "supplier_id": supplier_id},
    )
    assert premature.status_code == 409
    assert client.post(f"/requisitions/{requisition_id}/submit").json()["status"] == (
        "pending_approval"
    )
    assert client.post(f"/requisitions/{requisition_id}/submit").status_code == 409
    approved = client.post(
        f"/requisitions/{requisition_id}/approve",
        json={"approver": "finance-manager"},
    )
    assert approved.json()["status"] == "approved"

    order = client.post(
        "/purchase-orders",
        json={"requisition_id": requisition_id, "supplier_id": supplier_id},
    )
    assert order.status_code == 201
    assert order.json()["total_cents"] == 3750
    assert client.post(
        "/purchase-orders",
        json={"requisition_id": requisition_id, "supplier_id": supplier_id},
    ).status_code == 409
    listed = client.get("/purchase-orders", params={"status": "created"})
    assert [item["id"] for item in listed.json()] == [order.json()["id"]]


def test_generated_erp_api_rejects_unknown_and_invalid_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ErpPilotGenerator().generate(tmp_path)
    monkeypatch.setattr(sys, "path", [str(tmp_path / "src"), *sys.path])
    sys.modules.pop("erp_api", None)
    sys.modules.pop("erp_api.app", None)
    sys.modules.pop("erp_api.db", None)
    app_module = importlib.import_module("erp_api.app")
    client = TestClient(app_module.create_app(tmp_path / "runtime" / "erp.db"))

    invalid = client.post(
        "/requisitions",
        json={
            "requester": "alice",
            "justification": "ok",
            "items": [],
            "unexpected": True,
        },
    )
    assert invalid.status_code == 422
