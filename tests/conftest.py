import pytest


@pytest.fixture(autouse=True)
def _no_mail_relay(monkeypatch):
    """Tests never call the real mail relay; the relay test turns it back on itself."""
    monkeypatch.setenv("MAIL_RELAY", "off")
