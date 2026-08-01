from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from k8s_mcp.client import KubernetesReader, _as_text


def resource(name: str, namespace: str = "default") -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, namespace=namespace, creation_timestamp=None, labels={"app": "demo"}),
        status=SimpleNamespace(
            phase="Running",
            pod_ip="10.0.0.1",
            container_statuses=[SimpleNamespace(name="app", ready=True, restart_count=0, image="demo:v1", state=SimpleNamespace(running=object(), waiting=None, terminated=None))],
        ),
        spec=SimpleNamespace(node_name="node-a"),
    )


def test_list_pods_forwards_namespace_selector_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict = {}

    class FakeCoreV1Api:
        def __init__(self, _: object) -> None:
            pass

        def list_namespaced_pod(self, **kwargs: object) -> SimpleNamespace:
            calls.update(kwargs)
            return SimpleNamespace(items=[resource("demo-pod")])

    monkeypatch.setattr("k8s_mcp.client.client.CoreV1Api", FakeCoreV1Api)
    reader = KubernetesReader(api_client=object(), context="uat")

    result = reader.list_pods("ns-demo", "app=demo", 10)

    assert calls == {"namespace": "ns-demo", "label_selector": "app=demo", "limit": 10}
    assert result == [{"name": "demo-pod", "namespace": "default", "creation_timestamp": None, "labels": {"app": "demo"}, "phase": "Running", "pod_ip": "10.0.0.1", "node_name": "node-a", "containers": [{"name": "app", "ready": True, "restart_count": 0, "image": "demo:v1", "state": "running"}]}]


def test_get_pod_logs_disables_follow_and_limits_content(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict = {}

    class FakeCoreV1Api:
        def __init__(self, _: object) -> None:
            pass

        def read_namespaced_pod_log(self, **kwargs: object) -> str:
            calls.update(kwargs)
            return "line one"

    monkeypatch.setattr("k8s_mcp.client.client.CoreV1Api", FakeCoreV1Api)
    reader = KubernetesReader(api_client=object(), context="uat")

    assert reader.get_pod_logs("ns-demo", "demo-pod", "app", 5, 1_024)["logs"] == "line one"
    assert calls["follow"] is False
    assert calls["tail_lines"] == 5
    assert calls["limit_bytes"] == 1_024


def test_list_limit_rejects_excessive_value() -> None:
    reader = KubernetesReader(api_client=object(), context="uat")

    with pytest.raises(ValueError, match="500"):
        reader.list_pods("ns-demo", limit=501)


def test_as_text_formats_kubernetes_timestamp_in_shanghai_timezone() -> None:
    timestamp = datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc)

    assert _as_text(timestamp) == "2026-08-01 00:00:00"


def test_list_events_falls_back_to_core_v1_when_events_v1_has_missing_event_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    event = SimpleNamespace(
        metadata=SimpleNamespace(
            name="pod-failed", namespace="ns-demo", creation_timestamp=datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc), labels={}
        ),
        type="Warning",
        reason="BackOff",
        message="Back-off restarting failed container",
        involved_object=SimpleNamespace(kind="Pod", name="demo-pod"),
        last_timestamp=datetime(2026, 7, 31, 16, 1, tzinfo=timezone.utc),
    )

    class FakeEventsV1Api:
        def __init__(self, _: object) -> None:
            pass

        def list_namespaced_event(self, **_: object) -> SimpleNamespace:
            calls.append("events-v1")
            raise ValueError("Invalid value for `event_time`, must not be `None`")

    class FakeCoreV1Api:
        def __init__(self, _: object) -> None:
            pass

        def list_namespaced_event(self, **_: object) -> SimpleNamespace:
            calls.append("core-v1")
            return SimpleNamespace(items=[event])

    monkeypatch.setattr("k8s_mcp.client.client.EventsV1Api", FakeEventsV1Api)
    monkeypatch.setattr("k8s_mcp.client.client.CoreV1Api", FakeCoreV1Api)
    reader = KubernetesReader(api_client=object(), context="uat")

    assert reader.list_events("ns-demo", limit=10) == [
        {
            "name": "pod-failed",
            "namespace": "ns-demo",
            "creation_timestamp": "2026-08-01 00:00:00",
            "labels": {},
            "type": "Warning",
            "reason": "BackOff",
            "note": "Back-off restarting failed container",
            "regarding": {"kind": "Pod", "name": "demo-pod"},
            "event_time": "2026-08-01 00:01:00",
        }
    ]
    assert calls == ["events-v1", "core-v1"]


def test_list_and_get_config_map_return_non_secret_data(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    config_map = SimpleNamespace(
        metadata=SimpleNamespace(name="nginx-config", namespace="ns-demo", creation_timestamp=None, labels={}),
        data={"nginx.conf": "worker_processes 1;"},
        binary_data={"binary.conf": "YQ=="},
        immutable=True,
    )

    class FakeCoreV1Api:
        def __init__(self, _: object) -> None:
            pass

        def list_namespaced_config_map(self, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(items=[config_map])

        def read_namespaced_config_map(self, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            return config_map

    monkeypatch.setattr("k8s_mcp.client.client.CoreV1Api", FakeCoreV1Api)
    reader = KubernetesReader(api_client=object(), context="uat")

    assert reader.list_config_maps("ns-demo", "app=nginx", 10) == [
        {"name": "nginx-config", "namespace": "ns-demo", "creation_timestamp": None, "labels": {}, "data_keys": ["nginx.conf"], "binary_data_keys": ["binary.conf"], "immutable": True}
    ]
    assert reader.get_config_map("ns-demo", "nginx-config")["data"] == {"nginx.conf": "worker_processes 1;"}
    assert calls == [
        {"namespace": "ns-demo", "label_selector": "app=nginx", "limit": 10},
        {"name": "nginx-config", "namespace": "ns-demo"},
    ]


def test_get_pod_mounts_returns_references_without_secret_content(monkeypatch: pytest.MonkeyPatch) -> None:
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="nginx", namespace="ns-demo", creation_timestamp=None, labels={}),
        spec=SimpleNamespace(
            volumes=[
                SimpleNamespace(name="config", config_map=SimpleNamespace(name="nginx-config"), persistent_volume_claim=None, secret=None, projected=None),
                SimpleNamespace(name="tls", config_map=None, persistent_volume_claim=None, secret=SimpleNamespace(secret_name="nginx-tls"), projected=None),
            ],
            containers=[SimpleNamespace(name="nginx", volume_mounts=[SimpleNamespace(name="config", mount_path="/etc/nginx", read_only=True), SimpleNamespace(name="tls", mount_path="/etc/tls", read_only=True)])],
            init_containers=[],
        ),
    )

    class FakeCoreV1Api:
        def __init__(self, _: object) -> None:
            pass

        def read_namespaced_pod(self, **kwargs: object) -> SimpleNamespace:
            assert kwargs == {"name": "nginx", "namespace": "ns-demo"}
            return pod

    monkeypatch.setattr("k8s_mcp.client.client.CoreV1Api", FakeCoreV1Api)
    reader = KubernetesReader(api_client=object(), context="uat")

    result = reader.get_pod_mounts("ns-demo", "nginx")

    assert result["volumes"] == [
        {"name": "config", "source_type": "config_map", "source_name": "nginx-config"},
        {"name": "tls", "source_type": "secret", "source_name": "nginx-tls"},
    ]
    assert result["containers"] == [
        {"name": "nginx", "mounts": [{"volume_name": "config", "mount_path": "/etc/nginx", "read_only": True}, {"volume_name": "tls", "mount_path": "/etc/tls", "read_only": True}]}
    ]


def test_download_pod_logs_writes_to_safe_task_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeCoreV1Api:
        def __init__(self, _: object) -> None:
            pass

        def read_namespaced_pod_log(self, **kwargs: object) -> str:
            assert kwargs["follow"] is False
            assert kwargs["tail_lines"] == 5
            assert kwargs["limit_bytes"] == 1_024
            return "line one\n"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("k8s_mcp.client.client.CoreV1Api", FakeCoreV1Api)
    reader = KubernetesReader(api_client=object(), context="uat")

    result = reader.download_pod_logs(
        "ns/demo", "pod:one", "app/main", "incident/123", tail_lines=5, limit_bytes=1_024
    )

    output = Path(result["path"])
    assert output.is_relative_to(tmp_path / ".codex-tmp" / "k8s" / "incident_123")
    assert output.read_text(encoding="utf-8") == "line one\n"
    assert result["bytes_written"] == len("line one\n".encode("utf-8"))
    assert result["truncated"] is False


def test_download_pod_logs_marks_limit_sized_response_as_truncated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeCoreV1Api:
        def __init__(self, _: object) -> None:
            pass

        def read_namespaced_pod_log(self, **_: object) -> str:
            return "1234"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("k8s_mcp.client.client.CoreV1Api", FakeCoreV1Api)
    reader = KubernetesReader(api_client=object(), context="uat")

    result = reader.download_pod_logs("ns", "pod", "app", "task", limit_bytes=4)

    assert result["truncated"] is True


def test_download_pod_logs_rejects_path_traversal_task_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeCoreV1Api:
        def __init__(self, _: object) -> None:
            pass

        def read_namespaced_pod_log(self, **_: object) -> str:
            return "line"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("k8s_mcp.client.client.CoreV1Api", FakeCoreV1Api)
    reader = KubernetesReader(api_client=object(), context="uat")

    with pytest.raises(ValueError, match="task_id"):
        reader.download_pod_logs("ns", "pod", "app", "../../", limit_bytes=4)

    assert not (tmp_path / ".codex-tmp").exists()
