"""Tests for WhatsApp Monitor cloud sidecar launcher helpers."""

from __future__ import annotations

from whatsapp_monitor_launcher import find_backend_dir, find_node_bin, status


def test_find_backend_dir_in_repo():
    backend = find_backend_dir()
    assert backend is not None
    assert (backend / "src" / "index.js").is_file()


def test_status_reports_cloud_mode():
    snap = status()
    assert snap.get("mode") == "cloud_server"
    assert snap["port"] == 3100


def test_find_node_bin_optional():
    node = find_node_bin()
    assert node is None or isinstance(node, str)
