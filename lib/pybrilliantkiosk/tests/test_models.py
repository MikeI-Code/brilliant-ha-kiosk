from pybrilliantkiosk import PanelHealth


def test_health_helpers():
    units = {"ha-kiosk.service": "active", "x.service": "failed"}
    h = PanelHealth(1, 0, 0, 0, 1000, 250, "v1", units)
    assert h.mem_used_percent == 75.0
    assert h.unit_active("ha-kiosk")
    assert h.unit_active("ha-kiosk.service")
    assert not h.unit_active("x")
    assert not h.unit_active("missing")


def test_mem_unknown():
    assert PanelHealth(None, None, None, None, None, None, None).mem_used_percent is None
    assert PanelHealth(None, None, None, None, 0, 5, None).mem_used_percent is None
    assert PanelHealth(None, None, None, None, 10, None, None).mem_used_percent is None
