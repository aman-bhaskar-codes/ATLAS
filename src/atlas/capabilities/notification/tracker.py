"""Delivery Tracker + DeliveryHistory.

Records delivery receipts to SQLite.
"""

from __future__ import annotations

import json

from atlas.capabilities.notification.domain.models import DeliveryReceipt, Notification
from atlas.infra.db import Database


class DeliveryTracker:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, n: Notification, receipt: DeliveryReceipt) -> None:
        await self._db.conn.execute(
            "INSERT OR IGNORE INTO notif_history("
            "id, correlation_id, kind, priority, channels, delivered, final_provider, receipt, created_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (n.id, n.correlation_id, n.kind.value, int(n.priority),
             json.dumps(list(n.channels)),
             int(receipt.delivered),
             receipt.provider,
             receipt.model_dump_json(),
             n.created_ts.isoformat())
        )
        await self._db.conn.commit()
