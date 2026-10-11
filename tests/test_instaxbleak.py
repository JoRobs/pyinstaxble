from collections.abc import Callable
from dataclasses import dataclass

import pytest
from anyio import sleep as asleep
from bleak.exc import BleakError

from pyinstaxble import InstaxBLEAK
from pyinstaxble.instax_bleak import (
    NOTIFYCHAR_UUID,
    SERVICE_UUID,
    WRITECHAR_UUID,
)
from pyinstaxble.instax_types import PrinterSettings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def instaxbleak():
    return InstaxBLEAK(
        device_address=None,
        device_name=None,
        print_enabled=False,
        print_time_buffer=0,
        printer_settings=PrinterSettings.MINI,
    )


@pytest.fixture
def mock_instaxbleak(instaxbleak):
    @dataclass
    class MockBleakDevice:
        name = "mock_device"
        address = "MO:CK:DE:VI:CE"

    class MockBleakClient:
        """A mocked bleak client"""

        services: tuple
        services_disabled: bool = False
        is_connected: bool = True
        callback: Callable
        device: MockBleakDevice

        def __init__(self, device):
            self.device = device

        async def connect(self):
            await asleep(1)

        async def start_notify(self, char_specifier, callback):
            self.callback = callback
            await asleep(1)

        async def disconnect(self):
            await asleep(1)

        async def write_gatt_char(self, char_specifier, data, _):
            await asleep(1)
            if self.callback:
                self.callback(char_specifier, data)

        def disable_services(self):
            self.services_disabled = True

        @property
        def services(self):
            if self.services_disabled:
                raise BleakError()
            return (SERVICE_UUID, WRITECHAR_UUID, NOTIFYCHAR_UUID)

    instaxbleak.client = MockBleakClient(MockBleakDevice())
    return instaxbleak


def test_instaxbleak_init(instaxbleak: InstaxBLEAK):
    assert instaxbleak.print_enabled == False


async def test_is_connected(mock_instaxbleak):
    # false before connecting
    assert mock_instaxbleak.is_connected()


async def test_no_client_is_connected(instaxbleak):
    assert not instaxbleak.is_connected()


async def test_client_disconnect_is_connected(mock_instaxbleak):
    # false after connecting if client says so
    mock_instaxbleak.client.is_connected = False
    assert not mock_instaxbleak.is_connected()


async def test_no_service_is_connected(mock_instaxbleak):
    # false if services are not available
    mock_instaxbleak.client.disable_services()
    assert not mock_instaxbleak.is_connected()
