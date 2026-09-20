import bleak
import logging

from anyio import run

from instax_bleak import InstaxBLEAK

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

async def callback(char_uuid, data)->None:
  logger.info(char_uuid)
  logger.info(data)

async def main():
  # scanner = bleak.BleakScanner()
  # devices = await scanner.discover()

  # d = next(device for device in devices if device.name and device.name.endswith("(BLE)"))

  # async with bleak.BleakClient(d) as client:
  #     await client.start_notify("70954784-2d83-473d-9e5f-81e1d02d5273", callback)
  client = InstaxBLEAK()
  await client.connect(timeout=5)
  logger.info(client.battery_percentage)

run(main)
