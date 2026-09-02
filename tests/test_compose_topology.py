# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relative_path", ("compose.yaml", "deploy/airgap/compose.yaml"))
def test_compose_gateways_have_host_access_while_data_services_remain_internal(
    relative_path: str,
) -> None:
    document = yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))

    assert document["networks"]["backend"]["internal"] is True
    assert document["networks"]["host-access"] == {}
    for service_name in ("data-plane", "control-plane"):
        assert document["services"][service_name]["networks"] == ["backend", "host-access"]
    for service_name in ("postgres", "valkey"):
        assert document["services"][service_name]["networks"] == ["backend"]


@pytest.mark.parametrize(
    "relative_path",
    ("deploy/compose/provider-egress.yaml", "deploy/airgap/provider-egress.yaml"),
)
def test_provider_overlay_preserves_host_port_publication(relative_path: str) -> None:
    document = yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))

    assert document["services"]["data-plane"]["networks"] == [
        "backend",
        "host-access",
        "provider-egress",
    ]
