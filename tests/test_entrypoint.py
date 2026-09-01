# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest

from portable_ai_gateway import __main__ as entrypoint


@pytest.mark.parametrize("mode", ["data-plane", "control-plane"])
def test_server_entrypoints_disable_query_bearing_access_logs(
    mode, settings, monkeypatch
) -> None:
    captured: dict = {}

    monkeypatch.setattr(entrypoint.Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *args, **kwargs: captured.update(kwargs))

    assert entrypoint.main([mode, "--host", "127.0.0.1"]) == 0
    assert captured["access_log"] is False
