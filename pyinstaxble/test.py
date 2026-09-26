import logging

from anyio import run, sleep
from instax_bleak import InstaxBLEAK

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
    client = InstaxBLEAK(print_enabled=True)
    await client.connect(timeout=5)
    logger.info(client.battery_percentage)
    path = "/home/sma/Sync/Default/Photos/20250820_104150.jpg"
    await client.print_image(path)
    await sleep(60)


if __name__ == "__main__":
    run(main)
