from pathlib import Path
import sys
import ssl
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loki_mcp.client import _ssl_context, append_contains_filter, build_range_request, query_logs
from loki_mcp.config import LokiEnvironment


def test_append_contains_filter_escapes_logql_string() -> None:
    query = append_contains_filter('{app="mmom-kj"}', 'failed "order"')

    assert query == '{app="mmom-kj"} |= "failed \\"order\\""'


def test_build_range_request_uses_grafana_proxy_and_nanosecond_window() -> None:
    config = LokiEnvironment(
        name="矿机(一期生产)",
        base_url="https://mes.xcmg.com/grafana",
        datasource_uid="loki-uid",
        username="readonly",
        password="secret",
        query='{app="mmom-kj"}',
    )

    request = build_range_request(
        config,
        datetime(2026, 7, 29, 4, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 29, 4, 1, tzinfo=timezone.utc),
        limit=200,
        contains="error",
    )

    parsed = urlparse(request.full_url)
    parameters = parse_qs(parsed.query)
    assert parsed.path == "/grafana/api/datasources/proxy/uid/loki-uid/loki/api/v1/query_range"
    assert parameters["query"] == ['{app="mmom-kj"} |= "error"']
    assert parameters["start"] == ["1785297600000000000"]
    assert parameters["end"] == ["1785297660000000000"]
    assert parameters["limit"] == ["200"]
    assert request.get_header("Authorization").startswith("Basic ")


def test_production_mes_host_enables_legacy_tls_compatibility() -> None:
    config = LokiEnvironment(
        name="生产环境",
        base_url="https://mes.xcmg.com/grafana",
        datasource_uid="loki-uid",
        username="readonly",
        password="secret",
        query='{app="mes"}',
    )

    context = _ssl_context(config)

    assert context.options & ssl.OP_LEGACY_SERVER_CONNECT


def test_query_logs_returns_compact_entries_without_credentials() -> None:
    config = LokiEnvironment(
        name="矿机(一期生产)",
        base_url="https://mes.xcmg.com/grafana",
        datasource_uid="loki-uid",
        username="readonly",
        password="secret",
        query='{app="mmom-kj"}',
    )

    def opener(request, *, context, timeout):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"status":"success","data":{"resultType":"streams","result":[{"stream":{"pod":"pod-a","app":"mmom-kj"},"values":[["1785297600000000000","line one"]]}]}}'

        return Response()

    result = query_logs(
        config,
        datetime(2026, 7, 29, 4, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 29, 4, 1, tzinfo=timezone.utc),
        limit=200,
        contains=None,
        opener=opener,
    )

    assert result["status"] == "success"
    assert result["returned_entry_count"] == 1
    assert result["entries"] == [
        {
            "timestamp": "2026-07-29T04:00:00+00:00",
            "labels": {"pod": "pod-a", "app": "mmom-kj"},
            "line": "line one",
        }
    ]
    assert "secret" not in str(result)


def test_query_logs_preserves_nanosecond_timestamp_without_float_rounding() -> None:
    config = LokiEnvironment(
        name="矿机(一期生产)",
        base_url="https://mes.xcmg.com/grafana",
        datasource_uid="loki-uid",
        username="readonly",
        password="secret",
        query='{app="mmom-kj"}',
    )

    def opener(request, *, context, timeout):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"status":"success","data":{"resultType":"streams","result":[{"stream":{},"values":[["1785297653148000000","line"]]}]}}'

        return Response()

    result = query_logs(
        config,
        datetime(2026, 7, 29, 4, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 29, 4, 1, tzinfo=timezone.utc),
        opener=opener,
    )

    assert result["entries"][0]["timestamp"] == "2026-07-29T04:00:53.148000+00:00"
