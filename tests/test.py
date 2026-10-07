import logging
import random
from os.path import abspath

from anyio import create_task_group, get_cancelled_exc_class, run
from anyio import sleep as asleep

from pyinstaxble.exceptions import PrinterTimeoutError
from pyinstaxble.instax_bleak import InstaxBLEAK

logging.basicConfig(level=logging.DEBUG)

logging.getLogger("bleak").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


async def callback(char_uuid, data) -> None:
    logger.info(char_uuid)
    logger.info(data)


async def check_connected(client):
    rand = random.random()
    await asleep(rand)
    client.is_connected()


async def monitor_printer_info_loop(client: InstaxBLEAK):
    try:
        while True:
            try:
                await client.get_printer_info()
            except PrinterTimeoutError as e:
                logger.warning(f"Get info timed out {e}")
            await asleep(3)

    except get_cancelled_exc_class():
        logger.info("Stopping info monitoring")
        await client.disconnect()
        raise


async def main():
    try:
        # scanner = bleak.BleakScanner()
        # devices = await scanner.discover()

        # d = next(device for device in devices if device.name and device.name.endswith("(BLE)"))

        # async with bleak.BleakClient(d) as client:
        #     await client.start_notify("70954784-2d83-473d-9e5f-81e1d02d5273", callback)
        client = InstaxBLEAK(print_enabled=False)
        async with create_task_group() as tg:
            tg.start_soon(client.connect, 10)

        logger.info(f"Client connected: {client.is_connected()}")
        path = abspath("./resources/example-mini.jpg")
        try:
            async with create_task_group() as tg:
                tg.start_soon(monitor_printer_info_loop, client)

            async with create_task_group() as tg:
                await client.print_image(path, 1)

            async with create_task_group() as tg:
                await client.print_image(path, 60)

        except* PrinterTimeoutError as excgroup:
            for e in excgroup.exceptions:
                logger.exception("Task group failed")

    except KeyboardInterrupt:
        logger.warning("Interupted")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        run(main)
    except KeyboardInterrupt:
        logger.warning("Interupted")
