import logging
from datetime import datetime

from anyio import run

from pyinstaxble.instax_bleak import InstaxBLEAK

logging.basicConfig(level=logging.DEBUG)

logging.getLogger("bleak").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


async def callback(char_uuid, data) -> None:
    logger.info(char_uuid)
    logger.info(data)


async def main():
    # scanner = bleak.BleakScanner()
    # devices = await scanner.discover()

    # d = next(device for device in devices if device.name and device.name.endswith("(BLE)"))

    # async with bleak.BleakClient(d) as client:
    #     await client.start_notify("70954784-2d83-473d-9e5f-81e1d02d5273", callback)
    client = InstaxBLEAK(print_enabled=False)
    await client.connect(timeout=5)
    path = "/home/sma/Pictures/16550_main_l-900x563.jpg"
    start_time = datetime.now()
    await client.print_image(path)
    end_time = datetime.now()
    logger.info(f"Time to print: {end_time - start_time}")
    await client.disconnect()


if __name__ == "__main__":
    run(main)
