from app.health import health


def test_health_reports_ok():
    h = health()
    assert h.status == "ok"
    assert h.version
