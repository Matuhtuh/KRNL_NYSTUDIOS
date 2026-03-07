from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent.baritone import BaritoneBridge


class BridgeCommandAckTest(unittest.TestCase):
    def test_bridge_command_accepts_hashed_outbox_ack(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge_dir = Path(tmpdir)
            outbox_dir = bridge_dir / "outbox"
            outbox_dir.mkdir(parents=True, exist_ok=True)

            bridge = BaritoneBridge(
                controller=None,  # type: ignore[arg-type]
                perception=None,  # type: ignore[arg-type]
                config=SimpleNamespace(
                    bridge_dir=str(bridge_dir),
                    bridge_ack_timeout_seconds=0.25,
                ),
            )

            original_write_json_atomic = bridge._write_json_atomic

            def write_json_with_hashed_ack(path: Path, payload: dict[str, object]) -> None:
                original_write_json_atomic(path, payload)
                if path.parent.name != "inbox":
                    return
                ack_path = outbox_dir / f"{payload['id']}_hashed.json"
                ack_path.write_text(
                    json.dumps(
                        {
                            "id": payload["id"],
                            "command": payload["command"],
                            "status": "accepted",
                            "accepted": True,
                            "error": "",
                            "trackActive": payload["trackActive"],
                            "bridgeVersion": "0.1.8",
                        }
                    ),
                    encoding="utf-8",
                )

            bridge._write_json_atomic = write_json_with_hashed_ack  # type: ignore[method-assign]
            bridge.read_bridge_status = lambda: None  # type: ignore[method-assign]

            with mock.patch("agent.baritone.uuid.uuid4", return_value=SimpleNamespace(hex="abc123")):
                command_id = bridge._bridge_command(
                    "bridge.drop_item 28 all",
                    track_active=False,
                    ack_timeout_seconds=0.25,
                )

            self.assertEqual("abc123", command_id)
            self.assertFalse((outbox_dir / "abc123_hashed.json").exists())


if __name__ == "__main__":
    unittest.main()
