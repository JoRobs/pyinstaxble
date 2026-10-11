from collections.abc import Callable
from dataclasses import dataclass

import pytest
from anyio import sleep as asleep
from bleak.exc import BleakError

from pyinstaxble import InstaxBLEAK
from pyinstaxble.exceptions import (
    PrinterNotConnectedError,
    PrinterOutOfFilmError,
)
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
def mock_instaxbleak(monkeypatch):
    instaxbleak = InstaxBLEAK(
        device_address=None,
        device_name=None,
        print_enabled=False,
        print_time_buffer=0,
        printer_settings=PrinterSettings.MINI,
    )

    @dataclass
    class MockBleakDevice:
        name = "mock_device"
        address = "MO:CK:DE:VI:CE"

    class MockBleakClient:
        """A mocked bleak client"""

        services_disabled: bool = False
        is_connected: bool = True
        callback: Callable
        device: MockBleakDevice
        services: tuple = (SERVICE_UUID, WRITECHAR_UUID, NOTIFYCHAR_UUID)

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

        # def mock_disable_services(self):
        #     self.services_disabled = True

    monkeypatch.setattr(instaxbleak, "client", MockBleakClient)
    return instaxbleak


async def test_is_connected(mock_instaxbleak):
    """True when client says it is connected"""
    assert mock_instaxbleak.is_connected()


async def test_is_connected_no_client(mock_instaxbleak):
    """False when client says it is not connected, before connect()"""
    mock_instaxbleak.client.is_connected = False
    assert not mock_instaxbleak.is_connected()


async def test_is_connected_client_disconnected(mock_instaxbleak):
    """False when client says it is not connected"""
    mock_instaxbleak.client.is_connected = False
    assert not mock_instaxbleak.is_connected()


async def test_is_connected_client_no_services(mock_instaxbleak, monkeypatch):
    """False if client raises services are not available"""

    class MockBleakClient:
        is_connected = True

        @property
        def services(self) -> tuple:
            raise BleakError()

    monkeypatch.setattr(mock_instaxbleak, "client", MockBleakClient())
    assert not mock_instaxbleak.is_connected()


async def test_print_not_connected(mock_instaxbleak):
    """If we try to print when not connected, raise PrinterNotConnectedError"""
    mock_instaxbleak.is_connected = lambda: False

    with pytest.raises(PrinterNotConnectedError):
        await mock_instaxbleak.print_image("")


async def test_print_no_film(mock_instaxbleak):
    """If we try to when there is no film, raise PrinterOutOfFilmError"""
    mock_instaxbleak.film_remaining = 0
    mock_instaxbleak.print_enabled = True

    with pytest.raises(PrinterOutOfFilmError):
        await mock_instaxbleak.print_image("")
