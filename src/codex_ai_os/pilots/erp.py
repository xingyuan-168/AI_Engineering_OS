"""Generate the ERP procurement API fixture from governed templates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codex_ai_os.infrastructure.documents import DocumentManager


@dataclass(frozen=True, slots=True)
class ErpPilotGeneration:
    root: Path
    created_or_updated: tuple[str, ...]


class ErpPilotGenerator:
    def generate(self, project_root: Path) -> ErpPilotGeneration:
        root = project_root.resolve()
        documents = DocumentManager(root)
        written: list[str] = []
        for relative, content in ERP_PILOT_FILES.items():
            target = root / relative
            if not target.is_file() or target.read_text(encoding="utf-8") != content:
                documents.write_atomic(relative, content, overwrite=True)
                written.append(relative)
        return ErpPilotGeneration(root=root, created_or_updated=tuple(sorted(written)))


ERP_PILOT_FILES = {
    "pyproject.toml": """[project]
name = "erp-procurement-pilot"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = ["fastapi==0.141.1"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""",
    "src/erp_api/__init__.py": '"""ERP procurement pilot API."""\n',
    "src/erp_api/db.py": '''"""SQLite connections and forward-only schema migration."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);
CREATE TABLE IF NOT EXISTS purchase_requisitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requester TEXT NOT NULL,
    justification TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'pending_approval', 'approved', 'rejected')),
    approver TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TEXT
);
CREATE TABLE IF NOT EXISTS requisition_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requisition_id INTEGER NOT NULL REFERENCES purchase_requisitions(id),
    sku TEXT NOT NULL,
    description TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0)
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requisition_id INTEGER NOT NULL UNIQUE REFERENCES purchase_requisitions(id),
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'submitted', 'cancelled')),
    total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_requisitions_status
    ON purchase_requisitions(status);
CREATE INDEX IF NOT EXISTS ix_purchase_orders_status
    ON purchase_orders(status);
"""


def migrate(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        connection.commit()


@contextmanager
def connect(path: Path) -> Generator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        yield connection
    finally:
        connection.close()
''',
    "src/erp_api/app.py": '''"""FastAPI ERP procurement vertical slice."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from erp_api.db import connect, migrate


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupplierCreate(StrictModel):
    code: str = Field(pattern=r"^[A-Z0-9-]{2,20}$")
    name: str = Field(min_length=1, max_length=120)


class RequisitionItemCreate(StrictModel):
    sku: str = Field(pattern=r"^[A-Z0-9-]{2,40}$")
    description: str = Field(min_length=1, max_length=240)
    quantity: int = Field(gt=0, le=100000)
    unit_price_cents: int = Field(ge=0, le=1_000_000_000)


class RequisitionCreate(StrictModel):
    requester: str = Field(min_length=1, max_length=120)
    justification: str = Field(min_length=3, max_length=1000)
    items: list[RequisitionItemCreate] = Field(min_length=1, max_length=100)


class ApprovalCreate(StrictModel):
    approver: str = Field(min_length=1, max_length=120)


class PurchaseOrderCreate(StrictModel):
    requisition_id: int = Field(gt=0)
    supplier_id: int = Field(gt=0)


def create_app(database_path: Path) -> FastAPI:
    migrate(database_path)
    app = FastAPI(title="ERP Procurement Pilot", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/suppliers", status_code=status.HTTP_201_CREATED)
    def create_supplier(payload: SupplierCreate) -> dict[str, object]:
        try:
            with connect(database_path) as connection:
                cursor = connection.execute(
                    "INSERT INTO suppliers(code, name) VALUES (?, ?)",
                    (payload.code, payload.name),
                )
                connection.commit()
                supplier_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="supplier code already exists") from exc
        return {"id": supplier_id, **payload.model_dump(), "active": True}

    @app.post("/requisitions", status_code=status.HTTP_201_CREATED)
    def create_requisition(payload: RequisitionCreate) -> dict[str, object]:
        with connect(database_path) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "INSERT INTO purchase_requisitions(requester, justification) "
                    "VALUES (?, ?)",
                    (payload.requester, payload.justification),
                )
                requisition_id = int(cursor.lastrowid)
                connection.executemany(
                    "INSERT INTO requisition_items("
                    "requisition_id, sku, description, quantity, unit_price_cents"
                    ") VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            requisition_id,
                            item.sku,
                            item.description,
                            item.quantity,
                            item.unit_price_cents,
                        )
                        for item in payload.items
                    ],
                )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise
        return {"id": requisition_id, "status": "draft", **payload.model_dump()}

    @app.post("/requisitions/{requisition_id}/submit")
    def submit_requisition(requisition_id: int) -> dict[str, object]:
        return _transition_requisition(
            database_path,
            requisition_id,
            expected="draft",
            target="pending_approval",
        )

    @app.post("/requisitions/{requisition_id}/approve")
    def approve_requisition(
        requisition_id: int, payload: ApprovalCreate
    ) -> dict[str, object]:
        with connect(database_path) as connection:
            cursor = connection.execute(
                "UPDATE purchase_requisitions "
                "SET status = 'approved', approver = ?, approved_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'pending_approval'",
                (payload.approver, requisition_id),
            )
            connection.commit()
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail="requisition is not pending approval")
        return {"id": requisition_id, "status": "approved", "approver": payload.approver}

    @app.post("/purchase-orders", status_code=status.HTTP_201_CREATED)
    def create_purchase_order(payload: PurchaseOrderCreate) -> dict[str, object]:
        with connect(database_path) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                supplier = connection.execute(
                    "SELECT id FROM suppliers WHERE id = ? AND active = 1",
                    (payload.supplier_id,),
                ).fetchone()
                requisition = connection.execute(
                    "SELECT id FROM purchase_requisitions "
                    "WHERE id = ? AND status = 'approved'",
                    (payload.requisition_id,),
                ).fetchone()
                if supplier is None or requisition is None:
                    raise HTTPException(
                        status_code=409,
                        detail="active supplier and approved requisition are required",
                    )
                total = int(
                    connection.execute(
                        "SELECT COALESCE(SUM(quantity * unit_price_cents), 0) "
                        "FROM requisition_items WHERE requisition_id = ?",
                        (payload.requisition_id,),
                    ).fetchone()[0]
                )
                cursor = connection.execute(
                    "INSERT INTO purchase_orders("
                    "requisition_id, supplier_id, total_cents"
                    ") VALUES (?, ?, ?)",
                    (payload.requisition_id, payload.supplier_id, total),
                )
                connection.commit()
                order_id = int(cursor.lastrowid)
            except HTTPException:
                connection.rollback()
                raise
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise HTTPException(
                    status_code=409, detail="purchase order already exists"
                ) from exc
        return {
            "id": order_id,
            "requisition_id": payload.requisition_id,
            "supplier_id": payload.supplier_id,
            "status": "created",
            "total_cents": total,
        }

    @app.get("/purchase-orders")
    def list_purchase_orders(
        order_status: Annotated[
            Literal["created", "submitted", "cancelled"] | None,
            Query(alias="status"),
        ] = None,
    ) -> list[dict[str, object]]:
        query = "SELECT * FROM purchase_orders"
        parameters: tuple[object, ...] = ()
        if order_status is not None:
            query += " WHERE status = ?"
            parameters = (order_status,)
        query += " ORDER BY id"
        with connect(database_path) as connection:
            return [dict(row) for row in connection.execute(query, parameters).fetchall()]

    return app


def _transition_requisition(
    database_path: Path,
    requisition_id: int,
    *,
    expected: str,
    target: str,
) -> dict[str, object]:
    with connect(database_path) as connection:
        cursor = connection.execute(
            "UPDATE purchase_requisitions SET status = ? WHERE id = ? AND status = ?",
            (target, requisition_id, expected),
        )
        connection.commit()
    if cursor.rowcount != 1:
        raise HTTPException(status_code=409, detail=f"requisition is not {expected}")
    return {"id": requisition_id, "status": target}
''',
    "tests/test_api.py": '''from pathlib import Path

from fastapi.testclient import TestClient

from erp_api.app import create_app


def test_procurement_flow(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "erp.db"))
    supplier = client.post(
        "/suppliers", json={"code": "SUP-001", "name": "Supplier"}
    ).json()
    requisition = client.post(
        "/requisitions",
        json={
            "requester": "alice",
            "justification": "Replenish stock",
            "items": [
                {
                    "sku": "PART-001",
                    "description": "Part",
                    "quantity": 2,
                    "unit_price_cents": 500,
                }
            ],
        },
    ).json()
    client.post(f"/requisitions/{requisition['id']}/submit")
    client.post(
        f"/requisitions/{requisition['id']}/approve",
        json={"approver": "manager"},
    )
    order = client.post(
        "/purchase-orders",
        json={"requisition_id": requisition["id"], "supplier_id": supplier["id"]},
    )
    assert order.status_code == 201
    assert order.json()["total_cents"] == 1000
''',
    "docs/PRODUCT_REQUIREMENTS.md": """# 产品需求

## 目标

交付一个无 UI 的 ERP 采购 API，覆盖供应商、采购申请、审批、采购订单和状态查询。

## 验收标准

- 采购申请至少包含一个合法明细。
- 未提交的申请不能审批，未批准的申请不能生成采购订单。
- 同一申请只能生成一个采购订单，重复请求返回冲突。
- 所有金额以整数分存储，所有状态转换使用条件更新和事务。
""",
    "docs/API_SPEC.md": """# API 规格

## 端点

- `GET /health`
- `POST /suppliers`
- `POST /requisitions`
- `POST /requisitions/{id}/submit`
- `POST /requisitions/{id}/approve`
- `POST /purchase-orders`
- `GET /purchase-orders?status=created`

## 错误

输入错误返回 422；业务状态冲突或重复资源返回 409。
""",
    "docs/DATABASE.md": """# 数据库设计

SQLite 启用外键。核心表为 `suppliers`、`purchase_requisitions`、`requisition_items` 和 `purchase_orders`。金额使用整数分；申请到采购订单是一对一唯一约束；迁移使用 `CREATE TABLE/INDEX IF NOT EXISTS` 保证幂等。
""",
    "docs/ARCHITECTURE.md": """# 架构设计

FastAPI 负责 HTTP 和边界校验，Pydantic 严格拒绝未知字段，SQLite 负责事务、外键、唯一性和状态约束。应用采用工厂函数注入数据库路径，使测试与运行状态隔离。
""",
    "docs/SECURITY.md": """# 安全设计

- 请求模型拒绝未知字段并限制字符串、数量和金额范围。
- SQL 全部参数化，不接收任意查询或文件路径。
- 数据库事务使用 `BEGIN IMMEDIATE` 保护采购订单创建。
- 试点不接收凭据、不访问网络、不提供生产发布。
""",
    "docs/TEST_PLAN.md": """# 测试计划

覆盖健康检查、完整采购闭环、重复供应商、非法状态转换、未批准申请、重复采购订单、严格输入校验、迁移幂等和状态过滤。
""",
    "docs/OPEN_SOURCE_RESEARCH.md": """# 开源研究

## FastAPI

- 官方来源：https://github.com/fastapi/fastapi
- 版本：0.141.1
- License：MIT
- 用法：直接使用 HTTP 路由、OpenAPI 和 Pydantic 集成，不复制上游源码。

## HTTPX

- 官方来源：https://github.com/encode/httpx
- 版本：0.28.1
- License：BSD-3-Clause
- 用法：仅通过 FastAPI TestClient 用于测试。
""",
    "docs/CHANGELOG.md": """# 变更记录

## 0.1.0

- 增加 ERP 采购纵向 API、SQLite 幂等迁移和业务状态校验。
""",
}
