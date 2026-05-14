"""Unit tests for ``record_bash_usage`` — light-touch Bash-usage
telemetry marker. The detailed-aggregation path is exercised by
``get_session_metrics`` tests already; here we just pin the echo
contract + dispatcher wiring."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

from query_engine import QueryEngine


class RecordBashUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = QueryEngine(_SUBMODULE)

    def test_default_returns_ok_with_count_1(self) -> None:
        out = self.engine.record_bash_usage()
        self.assertEqual(out, {"ok": True, "count": 1})

    def test_custom_fields_echo_back(self) -> None:
        out = self.engine.record_bash_usage(count=5, action="grep", bytes_estimate=2048)
        self.assertEqual(out["count"], 5)
        self.assertEqual(out["action"], "grep")
        self.assertEqual(out["bytes_estimate"], 2048)

    def test_count_floored_at_1(self) -> None:
        self.assertEqual(self.engine.record_bash_usage(count=0)["count"], 1)
        self.assertEqual(self.engine.record_bash_usage(count=-3)["count"], 1)

    def test_dispatcher_wiring(self) -> None:
        """The MCP dispatcher routes ``record_bash_usage`` and the
        auto-record path (when a writer is attached) sees the result.
        """
        from mcp.dispatcher import Dispatcher

        writer = mock.MagicMock()
        dispatcher = Dispatcher(self.engine, metrics_writer=writer)
        result = dispatcher.call("record_bash_usage", {"count": 3, "action": "sed"})
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["action"], "sed")
        # MetricsWriter.record was invoked once for this call.
        self.assertEqual(writer.record.call_count, 1)
        # The first positional arg is the tool name.
        self.assertEqual(writer.record.call_args[0][0], "record_bash_usage")


if __name__ == "__main__":
    unittest.main()
