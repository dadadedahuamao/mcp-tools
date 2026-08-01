from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from k8s_mcp.config import (
    KubernetesConfigurationError,
    RegisteredCluster,
    load_cluster_registry,
    load_environment_registry,
    resolve_registered_cluster,
    validate_kubeconfig_path,
)


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


def test_load_cluster_registry_maps_alias_to_preconfigured_kubeconfig(tmp_path: Path) -> None:
    kubeconfig = tmp_path / "uat.yaml"
    kubeconfig.write_text("apiVersion: v1", encoding="utf-8")
    registry = tmp_path / "clusters.yaml"
    registry.write_text(f"clusters:\n  uat:\n    kubeconfig: {kubeconfig}\n    context: uat-context\n", encoding="utf-8")

    loaded = load_cluster_registry(registry.resolve())

    assert loaded["uat"].kubeconfig == kubeconfig
    assert loaded["uat"].context == "uat-context"


def test_load_cluster_registry_strips_alias_whitespace(tmp_path: Path) -> None:
    kubeconfig = tmp_path / "uat.yaml"
    kubeconfig.write_text("apiVersion: v1", encoding="utf-8")
    registry = tmp_path / "clusters.yaml"
    registry.write_text(f'clusters:\n  " 道路(二期UAT) ":\n    kubeconfig: {kubeconfig}\n', encoding="utf-8")

    loaded = load_cluster_registry(registry.resolve())

    assert list(loaded) == ["道路(二期UAT)"]


def test_load_environment_registry_maps_env_name_to_kubeconfig_basename(tmp_path: Path) -> None:
    kubeconfig = tmp_path / "cce-cloudpond-uat-mes-kubeconfig.yaml"
    kubeconfig.write_text("apiVersion: v1", encoding="utf-8")
    environment = tmp_path / "env.yaml"
    environment.write_text(
        "env:\n"
        "  - env_name: UAT\n"
        "    k8s_kubeconfig: .mcp/k8s-mcp/cce-cloudpond-uat-mes-kubeconfig.yaml\n"
        "  - env_name: Production\n",
        encoding="utf-8",
    )

    loaded = load_environment_registry(environment.resolve(), tmp_path.resolve())

    assert list(loaded) == ["UAT"]
    assert loaded["UAT"].kubeconfig == kubeconfig


def test_resolve_registered_cluster_accepts_unique_business_name(tmp_path: Path) -> None:
    kubeconfig = tmp_path / "road.yaml"
    kubeconfig.write_text("apiVersion: v1", encoding="utf-8")
    road = RegisteredCluster("道路(道路、筑路、养护)(二期UAT)", kubeconfig, None)

    resolved = resolve_registered_cluster({road.alias: road}, "二期道路环境")

    assert resolved is road


def test_resolve_registered_cluster_keeps_exact_official_name(tmp_path: Path) -> None:
    road = RegisteredCluster("道路(道路、筑路、养护)(二期UAT)", tmp_path / "road.yaml", None)

    assert resolve_registered_cluster({road.alias: road}, road.alias) is road


def test_resolve_registered_cluster_rejects_ambiguous_business_name(tmp_path: Path) -> None:
    first = RegisteredCluster("重型(一期UAT)", tmp_path / "first.yaml", None)
    second = RegisteredCluster("重型车辆(二期UAT)", tmp_path / "second.yaml", None)

    with pytest.raises(KubernetesConfigurationError, match="不唯一"):
        resolve_registered_cluster({first.alias: first, second.alias: second}, "重型")
