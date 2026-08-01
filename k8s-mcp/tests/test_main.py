from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
import types


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_main_module(monkeypatch):
    fake_config = types.ModuleType("k8s_mcp.config")
    fake_config.KubernetesConfigurationError = ValueError
    fake_config.load_cluster_registry = lambda _path: {}
    fake_config.load_environment_registry = lambda _env_file, _kubeconfig_dir: {}
    fake_server = types.ModuleType("k8s_mcp.server")
    fake_server.create_server = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "k8s_mcp.config", fake_config)
    monkeypatch.setitem(sys.modules, "k8s_mcp.server", fake_server)
    spec = importlib.util.spec_from_file_location("k8s_main_under_test", PROJECT_ROOT / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_passes_loaded_environment_clusters_to_server(monkeypatch) -> None:
    module = load_main_module(monkeypatch)
    registered_clusters = {"uat": object()}
    received: dict[str, object] = {}

    class FakeServer:
        def run(self, *, transport: str) -> None:
            received["transport"] = transport

    monkeypatch.setattr(module, "load_environment_registry", lambda _env_file, _kubeconfig_dir: registered_clusters)
    monkeypatch.setattr(
        module,
        "create_server",
        lambda clusters, **kwargs: received.update(clusters=clusters, **kwargs) or FakeServer(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--transport",
            "streamable-http",
            "--env-file",
            "/etc/k8s-mcp/env.yaml",
            "--kubeconfig-dir",
            "/etc/k8s-mcp",
        ],
    )

    module.main()

    assert received["clusters"] is registered_clusters
    assert received["transport"] == "streamable-http"
