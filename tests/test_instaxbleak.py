import pytest

from pyinstaxble import InstaxBLEAK
from pyinstaxble.instax_types import PrinterSettings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def instaxbleak_client():
    return InstaxBLEAK(
        device_address=None,
        device_name=None,
        print_enabled=False,
        print_time_buffer=0,
        printer_settings=PrinterSettings.MINI,
    )


def test_instaxbleak_init(instaxbleak_client: InstaxBLEAK):
    assert instaxbleak_client.print_enabled == False
