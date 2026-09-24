"""Tests for cloud WhatsApp Monitor reverse-proxy helpers."""

from __future__ import annotations

from whatsapp_monitor_proxy import BASE_PATH, WHATSAPP_UPSTREAM, _filter_request_headers


def test_proxy_defaults_are_localhost_sidecar():
    assert BASE_PATH == "/whatsapp-monitor"
    assert "127.0.0.1" in WHATSAPP_UPSTREAM or "localhost" in WHATSAPP_UPSTREAM


def test_filter_strips_hop_by_hop_headers():
    class H(dict):
        def items(self):
            return super().items()

    headers = H(
        {
            "Host": "example.com",
            "Connection": "keep-alive",
            "Accept": "application/json",
            "X-Request-Id": "abc",
        }
    )
    out = _filter_request_headers(headers)
    assert "Host" not in out and "host" not in {k.lower() for k in out}
    assert "Connection" not in out
    assert out.get("Accept") == "application/json"
    assert out.get("X-Request-Id") == "abc"
