from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from k8s_mcp.config import KubernetesConfigurationError, validate_kubeconfig_path


def test_validate_kubeconfig_path_rejects_relative_path() -> None:
    with pytest.raises(KubernetesConfigurationError, match="绝对"):
        validate_kubeconfig_path("kubeconfig.yaml")


def test_validate_kubeconfig_path_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(KubernetesConfigurationError, match="不存在"):
        validate_kubeconfig_path(str(tmp_path / "missing.yaml"))


def test_validate_kubeconfig_path_accepts_absolute_file(tmp_path: Path) -> None:
    kubeconfig = tmp_path / "config.yaml"
    kubeconfig.write_text("apiVersion: v1", encoding="utf-8")

    assert validate_kubeconfig_path(str(kubeconfig)) == kubeconfig

