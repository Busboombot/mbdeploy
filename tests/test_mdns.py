"""Unit tests for mbdeploy.mdns -- Advertiser and browse().

All tests run against fake ``zeroconf.Zeroconf``/``zeroconf.ServiceBrowser``
test doubles: no real network, no sleeping, no hardware. ``zeroconf.ServiceInfo``
itself is left real (it is a pure data holder, no I/O), so assertions about
what gets registered/discovered exercise the same object shape production
code does.
"""

from __future__ import annotations

from typing import Any

import pytest
import zeroconf

import mbdeploy.mdns as mdns_mod
from mbdeploy.mdns import Advertiser, browse

SERVICE_TYPE = "_mbtest._tcp.local."


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeZeroconf:
    """Records register/unregister/close calls; opens no real sockets.

    ``_network`` is a class-level dict simulating the shared mDNS "wire":
    every FakeZeroconf instance registers into and browses from the same
    dict, so a service registered via one instance (the Advertiser's) is
    discoverable via another (browse()'s), just as real zeroconf services
    are discoverable across separate Zeroconf instances on the same LAN.
    """

    _network: dict[str, Any] = {}
    instances: list["FakeZeroconf"] = []

    def __init__(self, interfaces=None, **kwargs) -> None:
        self.interfaces = interfaces
        self.registered: list[Any] = []
        self.unregistered: list[Any] = []
        self.closed = False
        self.close_count = 0
        FakeZeroconf.instances.append(self)

    def register_service(self, info, allow_name_change=False, **kwargs) -> None:
        self.registered.append(info)
        FakeZeroconf._network[info.name] = info

    def unregister_service(self, info) -> None:
        self.unregistered.append(info)
        FakeZeroconf._network.pop(info.name, None)

    def close(self) -> None:
        self.closed = True
        self.close_count += 1

    def get_service_info(self, type_, name, timeout=3000):
        return FakeZeroconf._network.get(name)


class FakeServiceBrowser:
    """Synchronously delivers every matching pre-registered fake discovery.

    Real zeroconf's ServiceBrowser calls the listener from a background
    thread shortly after construction; this fake does the equivalent
    synchronously at construction time so tests never sleep or wait on a
    thread.
    """

    instances: list["FakeServiceBrowser"] = []

    def __init__(self, zc, type_, listener) -> None:
        self.zc = zc
        self.type_ = type_
        self.listener = listener
        FakeServiceBrowser.instances.append(self)
        for name in list(FakeZeroconf._network):
            if name.endswith(type_):
                listener.add_service(zc, type_, name)


@pytest.fixture(autouse=True)
def _fake_zeroconf(monkeypatch):
    FakeZeroconf._network.clear()
    FakeZeroconf.instances.clear()
    FakeServiceBrowser.instances.clear()
    monkeypatch.setattr(mdns_mod.zeroconf, "Zeroconf", FakeZeroconf)
    monkeypatch.setattr(mdns_mod.zeroconf, "ServiceBrowser", FakeServiceBrowser)
    monkeypatch.setattr(mdns_mod.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(mdns_mod, "_local_ip", lambda: "127.0.0.1")
    yield


# ---------------------------------------------------------------------------
# TXT encode/decode helpers
# ---------------------------------------------------------------------------


def test_encode_txt_utf8_encodes_keys_and_values():
    encoded = mdns_mod._encode_txt({"uid": "abc123", "msg": "hello-mbdeploy-☃"})
    assert encoded == {
        b"uid": b"abc123",
        b"msg": "hello-mbdeploy-☃".encode("utf-8"),
    }


def test_encode_txt_handles_empty_value():
    assert mdns_mod._encode_txt({"empty": ""}) == {b"empty": b""}


def test_decode_txt_round_trips_non_ascii_and_empty():
    original = {"msg": "hello-mbdeploy-☃", "empty": ""}
    encoded = mdns_mod._encode_txt(original)
    assert mdns_mod._decode_txt(encoded) == original


def test_decode_txt_handles_none_value_and_missing_dict():
    assert mdns_mod._decode_txt(None) == {}
    assert mdns_mod._decode_txt({}) == {}
    assert mdns_mod._decode_txt({b"flag": None}) == {"flag": ""}


# ---------------------------------------------------------------------------
# Advertiser.register
# ---------------------------------------------------------------------------


def test_register_builds_service_info_with_correct_type_name_port():
    adv = Advertiser()
    handle = adv.register("gutov", SERVICE_TYPE, 9000, {"uid": "abc123"})

    zc = FakeZeroconf.instances[-1]
    assert len(zc.registered) == 1
    info = zc.registered[0]
    assert info.type == SERVICE_TYPE
    assert info.name == f"gutov.{SERVICE_TYPE}"
    assert info.port == 9000
    assert type(handle) is int
    adv.close()


def test_register_encodes_txt_dict_as_utf8_bytes():
    adv = Advertiser()
    txt = {
        "uid": "abc123",
        "role": "NEZHA2",
        "common_name": "gutov",
        "enum": "2",
        "port": "/dev/ttyACM0",
    }
    adv.register("gutov", SERVICE_TYPE, 9000, txt)

    info = FakeZeroconf.instances[-1].registered[0]
    assert info.properties == {
        b"uid": b"abc123",
        b"role": b"NEZHA2",
        b"common_name": b"gutov",
        b"enum": b"2",
        b"port": b"/dev/ttyACM0",
    }
    adv.close()


def test_register_handle_is_not_a_zeroconf_service_info_or_zeroconf():
    adv = Advertiser()
    handle = adv.register("gutov", SERVICE_TYPE, 9000, {})

    assert not isinstance(handle, zeroconf.ServiceInfo)
    assert not isinstance(handle, FakeZeroconf)
    adv.close()


def test_bind_addr_threads_into_zeroconf_interfaces():
    Advertiser(bind_addr="192.168.1.5")
    zc = FakeZeroconf.instances[-1]
    assert zc.interfaces == ["192.168.1.5"]


def test_no_bind_addr_uses_zeroconf_default_interfaces():
    Advertiser()
    zc = FakeZeroconf.instances[-1]
    assert zc.interfaces is None


# ---------------------------------------------------------------------------
# Advertiser.unregister / close
# ---------------------------------------------------------------------------


def test_unregister_removes_exactly_one_registration():
    adv = Advertiser()
    h1 = adv.register("gutov", SERVICE_TYPE, 9000, {"uid": "1"})
    adv.register("alpha", SERVICE_TYPE, 9001, {"uid": "2"})
    zc = FakeZeroconf.instances[-1]

    adv.unregister(h1)

    assert len(zc.unregistered) == 1
    assert zc.unregistered[0].name == f"gutov.{SERVICE_TYPE}"

    # The second registration must still be live -- close() should still
    # find and unregister it.
    adv.close()
    assert len(zc.unregistered) == 2
    assert {info.name for info in zc.unregistered} == {
        f"gutov.{SERVICE_TYPE}",
        f"alpha.{SERVICE_TYPE}",
    }


def test_unregister_twice_on_same_handle_does_not_raise():
    adv = Advertiser()
    handle = adv.register("gutov", SERVICE_TYPE, 9000, {})
    zc = FakeZeroconf.instances[-1]

    adv.unregister(handle)
    adv.unregister(handle)  # must not raise

    assert len(zc.unregistered) == 1


def test_close_unregisters_all_remaining_handles():
    adv = Advertiser()
    adv.register("gutov", SERVICE_TYPE, 9000, {})
    adv.register("alpha", SERVICE_TYPE, 9001, {})
    zc = FakeZeroconf.instances[-1]

    adv.close()

    assert len(zc.unregistered) == 2
    assert zc.close_count == 1


def test_close_is_idempotent():
    adv = Advertiser()
    adv.register("gutov", SERVICE_TYPE, 9000, {})
    zc = FakeZeroconf.instances[-1]

    adv.close()
    assert len(zc.unregistered) == 1
    assert zc.close_count == 1

    adv.close()  # second call: no further unregister/close activity
    assert len(zc.unregistered) == 1
    assert zc.close_count == 1


def test_close_after_partial_unregister_does_not_reraise_or_double_unregister():
    adv = Advertiser()
    handle = adv.register("gutov", SERVICE_TYPE, 9000, {})
    zc = FakeZeroconf.instances[-1]

    adv.unregister(handle)
    adv.close()  # nothing left to unregister

    assert len(zc.unregistered) == 1
    assert zc.close_count == 1


# ---------------------------------------------------------------------------
# browse()
# ---------------------------------------------------------------------------


def test_browse_returns_empty_list_when_nothing_registered():
    assert browse(SERVICE_TYPE, timeout=0.01) == []


def test_browse_returns_one_dict_per_discovered_service():
    adv = Advertiser()
    adv.register("gutov", SERVICE_TYPE, 9000, {"uid": "1"})

    results = browse(SERVICE_TYPE, timeout=0.01)

    assert results == [
        {
            "name": f"gutov.{SERVICE_TYPE}",
            "host": "127.0.0.1",
            "port": 9000,
            "txt": {"uid": "1"},
        }
    ]
    adv.close()


def test_browse_returns_multiple_discovered_services():
    adv = Advertiser()
    adv.register("gutov", SERVICE_TYPE, 9000, {"uid": "1"})
    adv.register("alpha", SERVICE_TYPE, 9001, {"uid": "2"})

    results = browse(SERVICE_TYPE, timeout=0.01)

    assert len(results) == 2
    names = {r["name"] for r in results}
    assert names == {f"gutov.{SERVICE_TYPE}", f"alpha.{SERVICE_TYPE}"}
    for result in results:
        assert set(result) == {"name", "host", "port", "txt"}
    adv.close()


def test_register_then_browse_round_trips_txt_str_values():
    """Acceptance criterion: the exact fixture from the ticket."""
    adv = Advertiser()
    txt = {
        "uid": "abc123",
        "role": "NEZHA2",
        "common_name": "gutov",
        "enum": "2",
        "port": "/dev/ttyACM0",
    }
    adv.register("gutov", SERVICE_TYPE, 9000, txt)

    results = browse(SERVICE_TYPE, timeout=0.01)

    assert len(results) == 1
    result = results[0]
    assert result["txt"] == txt
    for value in result["txt"].values():
        assert isinstance(value, str)
    adv.close()


def test_register_then_browse_round_trips_non_ascii_and_empty_values():
    adv = Advertiser()
    txt = {"msg": "hello-mbdeploy-☃", "empty": ""}
    adv.register("gutov", SERVICE_TYPE, 9000, txt)

    results = browse(SERVICE_TYPE, timeout=0.01)

    assert results[0]["txt"] == txt
    adv.close()
