from __future__ import annotations

from pipeline.selfcheck import self_check_offline
from tests.conftest import SRT


def test_self_check_offline_ok():
    self_check_offline(SRT)
