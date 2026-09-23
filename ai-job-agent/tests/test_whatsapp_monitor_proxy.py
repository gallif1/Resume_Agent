"""WhatsApp Monitor is client-local-agent mode (UI only on server)."""

from whatsapp_monitor_proxy import BASE_PATH, LOCAL_AGENT_URL, find_frontend_dist


def test_client_mode_defaults():
    assert BASE_PATH == "/whatsapp-monitor"
    assert "127.0.0.1" in LOCAL_AGENT_URL or "localhost" in LOCAL_AGENT_URL


def test_find_frontend_dist_optional():
    # Dist may or may not exist in CI before build — helper must not raise.
    dist = find_frontend_dist()
    assert dist is None or (dist / "index.html").is_file()
