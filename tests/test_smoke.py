"""Smoke tests — verify the test setup works."""
from __future__ import annotations


def test_can_import_integration():
    from custom_components.hisense_vrf import const
    assert const.DOMAIN == "hisense_vrf"


def test_can_import_pyacmodbus():
    import pyacmodbus
    assert hasattr(pyacmodbus, "ACModbusClient")
    assert hasattr(pyacmodbus, "BASE_ADDR")
    assert pyacmodbus.BASE_ADDR == 40000


async def test_hass_fixture(hass):
    """The HA fixture from pytest_homeassistant_custom_component."""
    assert hass is not None


async def test_setup_integration(hass, setup_integration):
    """Smoke test that the integration loads cleanly."""
    assert setup_integration.state.value == "loaded"
