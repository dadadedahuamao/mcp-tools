from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from k8s_mcp.client import KubernetesReader


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
