from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from k8s_mcp.server import create_server


def test_server_defaults_to_k8s_http_port() -> None:
    server = create_server()

    assert server.settings.port == 18002


def test_server_registers_only_read_only_tools() -> None:
    server = create_server()
    names = set(server._tool_manager._tools)

    assert names == {
        "get_cluster_info",
        "list_namespaces",
        "list_nodes",
        "list_pods",
        "get_pod",
        "list_deployments",
        "list_events",
        "list_config_maps",
        "get_config_map",
        "get_pod_mounts",
        "get_pod_logs",
        "download_pod_logs",
    }


def test_main_help_exits_without_starting_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    monkeypatch.setattr(sys, "argv", ["k8s-mcp", "--help"])

    with pytest.raises(SystemExit) as error:
        main.main()

    assert error.value.code == 0
