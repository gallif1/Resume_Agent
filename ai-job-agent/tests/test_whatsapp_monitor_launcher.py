"""Tests for WhatsApp Monitor sidecar launcher helpers."""

from __future__ import annotations

from whatsapp_monitor_launcher import find_backend_dir, find_node_bin, status


def test_find_backend_dir_in_repo():
    backend = find_backend_dir()
    assert backend is not None
    assert (backend / "src" / "index.js").is_file()


def test_status_shape():
    snap = status()
    assert "upstream_up" in snap
    assert "port" in snap
    assert snap["port"] == 3100


def test_find_node_bin_optional():
    # In CI/dev node is usually present; either way the helper must not raise.
    node = find_node_bin()
    assert node is None or isinstance(node, str)
