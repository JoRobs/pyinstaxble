import logging
import random
from datetime import datetime
from os.path import abspath

from anyio import create_task_group, run
from anyio import sleep as asleep

from pyinstaxble.instax_bleak import InstaxBLEAK

logging.basicConfig(level=logging.INFO)

logging.getLogger("bleak").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


async def callback(char_uuid, data) -> None:
    logger.info(char_uuid)
    logger.info(data)


async def check_connected(client):
    rand = random.random()
    await asleep(rand)
    client.is_connected()


async def main():
    # scanner = bleak.BleakScanner()
    # devices = await scanner.discover()

    # d = next(device for device in devices if device.name and device.name.endswith("(BLE)"))

    # async with bleak.BleakClient(d) as client:
    #     await client.start_notify("70954784-2d83-473d-9e5f-81e1d02d5273", callback)
    client = InstaxBLEAK(print_enabled=False)
    async with create_task_group() as tg:
        tg.start_soon(client.connect, 5)
        tg.start_soon(check_connected, client)
        tg.start_soon(check_connected, client)
        tg.start_soon(check_connected, client)

    logger.info(f"Client connected: {client.is_connected()}")
    path = abspath("./resources/example-mini.jpg")
    start_time = datetime.now()
    await client.print_image(path)
    end_time = datetime.now()
    logger.info(f"Time to print: {end_time - start_time}")
    await client.disconnect()


if __name__ == "__main__":
    run(main)
