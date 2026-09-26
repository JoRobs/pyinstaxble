import argparse
import logging
import sys
from io import BytesIO
from math import ceil
from struct import pack, unpack_from
from uuid import UUID

from anyio import sleep as asleep
from anyio import run, move_on_after
from bleak import BleakScanner, BleakClient, BLEDevice, AdvertisementData
from PIL import Image

import pyinstaxble.led_patterns as LedPatterns
from pyinstaxble.instax_types import (
    EventType,
    InfoType,
    PrinterSettingsData,
    PrinterSettings,
)

logger = logging.getLogger(__name__)

SERVICE_UUID = UUID("70954782-2d83-473d-9e5f-81e1d02d5273")
WRITECHAR_UUID = UUID("70954783-2d83-473d-9e5f-81e1d02d5273")
NOTIFYCHAR_UUID = UUID("70954784-2d83-473d-9e5f-81e1d02d5273")
INSTAX_DEVICE_NAME_PREFIX = "INSTAX-"
INSTAX_DEVICE_NAME_SUFFIX = "(BLE)"
MAX_PACKET_SIZE = 182


class InstaxBLEAK:
    printer_settings: PrinterSettingsData
    device_address: str | None
    device_name: str | None
    print_enabled: bool
    client: BleakClient | None = None

    def __init__(
        self,
        printer_settings: PrinterSettingsData | None = PrinterSettings.MINI,
        device_address: str = None,
        device_name: str = None,
        print_enabled: bool = False,
    ):
        """
        Initialize the InstaxBLE class.
        deviceAddress: if specified, will only connect to a printer with this address.
        printEnabled: by default, actual printing is disabled to prevent misprints.
        """
        # BLE
        self.peripheral = None

        self.printer_settings = printer_settings
        self.chunk_size: int = printer_settings.chunk_size
        self.print_enabled: bool = print_enabled
        self.device_name: str = device_name.upper() if device_name else None
        self.device_address: str = (
            device_address.upper() if device_address else None
        )
        self.packets_for_printing: list = []
        self.pos = (0, 0, 0, 0)
        self.battery_state = 0
        self.battery_percentage = 0
        self.photos_left = 0
        self.is_charging = False
        self.image_size = (printer_settings.width, printer_settings.height)
        self.cancelled = False
        self.scanner = BleakScanner(self.detection_callback)

        self.awaiting_response = False
        self.awaiting_info_battery = False
        self.awaiting_info_image = False
        self.awaiting_info_printfunc = False

    def detection_callback(self, device, data) -> None:
        if not data.local_name and device.name:
            logger.debug(
                "Detected BLE advertisement, but not enough data to log"
            )
            return

        logger.debug(
            f"Device {device.name} detected BLE advertisement {data.local_name}"
        )

    async def get_printer_info(self, timeout=5, poll_delay=0.05):
        """Get and display the printer's status and info, like photos left and battery level"""

        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.IMAGE_SUPPORT_INFO.value),
        )
        self.awaiting_info_image = True
        await self.send_packet(packet)

        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.BATTERY_INFO.value),
        )
        self.awaiting_info_battery = True
        await self.send_packet(packet)

        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.PRINTER_FUNCTION_INFO.value),
        )
        self.awaiting_info_printfunc = True
        await self.send_packet(packet)

        with move_on_after(timeout):
            while (
                self.awaiting_info_battery
                or self.awaiting_info_image
                or self.awaiting_info_printfunc
            ) and not self.cancelled:
                await asleep(poll_delay)

    def display_current_status(self):
        """Display an overview of the current printer state"""

        status_string = f"""
Printer details:
Model:               {self.printer_settings.model_name}
Photos left:         {self.photos_left}/10
Battery level:       {self.battery_percentage}%
Charging:            {self.is_charging}
Required image size: {self.printer_settings.width}px, {self.printer_settings.height}px
        """
        logger.info(status_string)

    def unawait_response(func):
        def wrapper(self, *args, **kwargs):
            try:
                res = func(self, *args, **kwargs)
                return res
            finally:
                self.awaiting_response = False

        return wrapper

    @unawait_response
    async def parse_printer_response(self, event, packet):
        """Parse the response packet and print the result"""
        logger.debug(f"Parsing printer info: event: {event}, packet: {packet}")

        if event == EventType.XYZ_AXIS_INFO:
            x, y, z, o = unpack_from("<hhhB", packet[6:-1])
            self.pos = (x, y, z, o)
        elif event == EventType.LED_PATTERN_SETTINGS:
            pass
        elif event == EventType.SUPPORT_FUNCTION_INFO:
            try:
                infoType = InfoType(packet[7])
            except ValueError:
                logger.debug(f"Unknown InfoType: {packet[7]}")
                return

            logger.debug(f"Info type: {infoType}")

            if infoType == InfoType.IMAGE_SUPPORT_INFO:
                w, h = unpack_from(">HH", packet[8:12])
                self.image_size = (w, h)
                if (w, h) == (600, 800):
                    self.printer_settings = PrinterSettings.MINI
                elif (w, h) == (800, 800):
                    self.printer_settings = PrinterSettings.SQUARE
                elif (w, h) == (1260, 840):
                    self.printer_settings = PrinterSettings.WIDE
                else:
                    sys.exit(f"Unknown image size from printer: {w}x{h}")

                self.chunk_size = self.printer_settings.chunk_size
                self.awaiting_info_image = False

            elif infoType == InfoType.BATTERY_INFO:
                self.battery_state, self.battery_percentage = unpack_from(
                    ">BB", packet[8:10]
                )
                self.awaiting_info_battery = False

            elif infoType == InfoType.PRINTER_FUNCTION_INFO:
                dataByte = packet[8]
                self.photos_left = dataByte & 15
                self.is_charging = (1 << 7) & dataByte >= 1
                self.awaiting_info_printfunc = False

        elif (
            event == EventType.PRINT_IMAGE_DOWNLOAD_START
            or event == EventType.PRINT_IMAGE_DOWNLOAD_DATA
            or event == EventType.PRINT_IMAGE_DOWNLOAD_END
        ):
            await self.handle_image_packet_queue()

        elif event == EventType.PRINT_IMAGE_DOWNLOAD_CANCEL:
            pass

        elif event == EventType.PRINT_IMAGE:
            logger.debug("received print confirmation")

        else:
            logger.error(f"Unknown response from printer. Eventype: {event}")

    async def handle_image_packet_queue(self):
        if len(self.packets_for_printing) > 0 and not self.cancelled:
            if len(self.packets_for_printing) % 10 == 0:
                logger.debug(
                    f"Img packets left to send: {len(self.packets_for_printing)}"
                )
            packet = self.packets_for_printing.pop(0)
            await self.send_packet(packet)

    async def notification_handler(self, char_uuid, packet) -> None:
        """Gets called whenever the printer replies and handles parsing the received data"""
        logger.debug(f"Char: {char_uuid}")
        logger.debug(f"Bytes: {packet}")

        if len(packet) < 8:
            logger.error(
                f"\tError: response packet size should be >= 8 (was {len(packet)})!"
            )
            return
        elif not self.validate_checksum(packet):
            logger.error("\tResponse packet checksum was invalid!")
            return

        _header, _length, op1, op2 = unpack_from(">HHBB", packet)
        # logger.info('\theader: ', header, '\t', self.prettify_bytearray(packet[0:2]))
        # logger.info('\tlength: ', length, '\t', self.prettify_bytearray(packet[2:4]))
        # logger.info('\top1: ', op1, '\t\t', self.prettify_bytearray(packet[4:5]))
        # logger.info('\top2: ', op2, '\t\t', self.prettify_bytearray(packet[5:6]))

        try:
            event = EventType((op1, op2))
        except ValueError:
            logger.error(f"Unknown EventType: ({op1}, {op2})")
            return

        await self.parse_printer_response(event, packet)

    async def find_device(self, timeout=5) -> BLEDevice | None:
        """ " Scan for our device and return it when found"""
        logger.debug("Searching for instax printer...")

        def device_filter(device: BLEDevice, data: AdvertisementData):
            """Filter scanned devices for instax printer"""

            if self.device_address:
                return self.device_address == device.address

            if self.device_name:
                return self.device_name == device.name
                return self.device_name == data.local_name
            if device.name:
                return device.name.startswith(
                    INSTAX_DEVICE_NAME_PREFIX
                ) and device.name.endswith(INSTAX_DEVICE_NAME_SUFFIX)
            return False

        try:
            device = await self.scanner.find_device_by_filter(
                device_filter, timeout
            )
            if device:
                return device
            search_criteria = next(
                i
                for i in (
                    self.device_name,
                    self.device_address,
                    f"{INSTAX_DEVICE_NAME_PREFIX}______{INSTAX_DEVICE_NAME_SUFFIX}",
                )
                if i
            )
            logger.error(f"Device {search_criteria} was not found during scan")
        except Exception as e:
            logger.error(e)

    async def connect(self, timeout=5):
        """Connect to the printer. Stops trying after the timeout."""

        device = await self.find_device(timeout=timeout)

        if device:
            logger.info(f"Connecting to {device.name} [{device.address}]")
            self.client = BleakClient(device)

            try:
                await self.client.connect()
            except Exception as e:
                logger.error(f"Error connecting to {device.name}: {e}")

            logger.info("Connected")

            try:
                await self.client.start_notify(
                    17, callback=self.notification_handler
                )
            except Exception as e:
                logger.error(f"Error on attaching notification_handler: {e}")
                return

            await self.get_printer_info(timeout)
            self.display_current_status()

        else:
            logger.debug("No connectable device found.")

    async def disconnect(self):
        """Disconnect from the printer (if connected)"""

        if not self.client:
            return

        if len(self.packetsForPrinting) > 0 and not self.cancelled:
            logger.info("sending cancel command")
            await self.send_packet(
                self.create_packet(EventType.PRINT_IMAGE_DOWNLOAD_CANCEL)
            )

        logger.info("Disconnecting...")
        await self.client.disconnect()
        logger.info("Disconnected")

    async def cancel_print(self):
        self.packets_for_printing = []
        self.awaiting_response = False
        await self.send_packet(
            self.create_packet(EventType.PRINT_IMAGE_DOWNLOAD_CANCEL)
        )

    def enable_printing(self):
        """Enable printing."""
        self.print_enabled = True

    def disable_printing(self):
        """Disable printing."""
        self.print_enabled = False

    def create_color_payload(self, colorArray, speed, repeat, when):
        """
        Create a payload for a color pattern. See send_led_pattern for details.
        """
        payload = pack("BBBB", when, len(colorArray), speed, repeat)
        for color in colorArray:
            payload += pack("BBB", color[0], color[1], color[2])
        return payload

    async def send_led_pattern(self, pattern, speed=5, repeat=255, when=0):
        """Send a LED pattern to the Instax printer.
        colorArray: array of BGR(!) values to use in animation, e.g. [[255, 0, 0], [0, 255, 0], [0, 0, 255]]
        speed: time per frame/color: higher is slower animation
        repeat: 0 = don't repeat (so play once), 1-254 = times to repeat, 255 = repeat forever
        when: 0 = normal, 1 = on print, 2 = on print completion, 3 = pattern switch"""
        payload = self.create_color_payload(pattern, speed, repeat, when)
        packet = self.create_packet(EventType.LED_PATTERN_SETTINGS, payload)
        await self.send_packet(packet)

    def prettify_bytearray(self, value):
        """Helper funtion to convert a bytearray to a string of hex values."""
        return " ".join([f"{x:02x}" for x in value])

    def create_checksum(self, bytearray):
        """Create a checksum for a given packet."""
        return (255 - (sum(bytearray) & 255)) & 255

    def create_packet(self, eventType, payload=b""):
        """Create a packet to send to the printer."""
        if isinstance(
            eventType, EventType
        ):  # allows passing in an event or a value directly
            eventType = eventType.value

        header = b"\x41\x62"  # 'Ab' means client to printer, 'aB' means printer to client
        opCode = bytes([eventType[0], eventType[1]])
        packetSize = pack(">H", 7 + len(payload))
        packet = header + packetSize + opCode + payload
        packet += pack("B", self.create_checksum(packet))
        return packet

    def validate_checksum(self, packet):
        """Validate the checksum of a packet."""
        return (sum(packet) & 255) == 255

    async def send_packet(self, packet, timeout=10, poll_delay=0.10):
        """Send a packet to the printer"""
        # logger.debug(f"Start sending packet: {packet}, will wait for other packets...")

        if not self.client:
            logger.error("No connected device, run connect first.")
            return

        with move_on_after(timeout):
            while self.awaiting_response and not self.cancelled:
                await asleep(poll_delay)

        try:
            logger.debug("Finished waiting")

            _header, _length, op1, op2 = unpack_from(">HHBB", packet)
            try:
                EventType((op1, op2))
            except Exception as e:
                logger.error(e.with_traceback())

            self.awaiting_response = True
            numberOfParts = ceil(len(packet) / MAX_PACKET_SIZE)
            logger.debug(f"> Number of parts to send: {numberOfParts}")
            for subPartIndex in range(numberOfParts):
                logger.debug(
                    f"> Sending part {subPartIndex + 1}/{numberOfParts}"
                )
                subPacket = packet[
                    subPartIndex * MAX_PACKET_SIZE : subPartIndex
                    * MAX_PACKET_SIZE
                    + MAX_PACKET_SIZE
                ]

                await self.client.write_gatt_char(
                    WRITECHAR_UUID, subPacket, response=False
                )

        except KeyboardInterrupt:
            self.cancelled = True
            self.cancel_print()
            # sleep(1)
            self.disconnect()
            sys.exit("Cancelled")

    async def print_image(self, imgSrc, timeout=20, poll_delay=0.10):
        """
        print an image. Either pass a path to an image (as a string) or pass
        the bytearray to print directly
        """
        logger.info(f'Printing image "{imgSrc}"')

        if self.photos_left == 0:
            logger.error("Cannot print: no film left in printer.")
            return

        imgData = imgSrc
        if isinstance(imgSrc, str):  # if it's a path, load the image contents
            image = Image.open(imgSrc)
            imgData = self.pil_image_to_bytes(image, max_size_kb=105)
        elif isinstance(imgSrc, BytesIO):
            imgSrc.seek(0)  # Go to the start of the BytesIO object
            image = Image.open(imgSrc)
            imgData = self.pil_image_to_bytes(image, max_size_kb=105)

        # logger.info(f"len of imagedata: {len(imgData)}")
        self.packets_for_printing = [
            # \x02\x00\x00\x00 payload made of four bytes: pictureType, picturePrintOption, picturePrintOption2, zero
            self.create_packet(
                EventType.PRINT_IMAGE_DOWNLOAD_START,
                b"\x02\x00\x00\x00" + pack(">I", len(imgData)),
            )
        ]

        # divide image data up into chunks of <chunkSize> bytes and pad the last chunk with zeroes if needed
        imgDataChunks = [
            imgData[i : i + self.chunk_size]
            for i in range(0, len(imgData), self.chunk_size)
        ]
        if len(imgDataChunks[-1]) < self.chunk_size:
            imgDataChunks[-1] = imgDataChunks[-1] + bytes(
                self.chunk_size - len(imgDataChunks[-1])
            )

        # create a packet from each of our chunks, this includes adding the chunk number
        for index, chunk in enumerate(imgDataChunks):
            imgDataChunks[index] = (
                pack(">I", index) + chunk
            )  # add chunk number as int (4 bytes)
            self.packets_for_printing.append(
                self.create_packet(
                    EventType.PRINT_IMAGE_DOWNLOAD_DATA, imgDataChunks[index]
                )
            )

        self.packets_for_printing.append(
            self.create_packet(EventType.PRINT_IMAGE_DOWNLOAD_END)
        )

        if self.print_enabled:
            self.packets_for_printing.append(
                self.create_packet(EventType.PRINT_IMAGE)
            )
            self.packets_for_printing.append(
                self.create_packet((0, 2), b"\x02")
            )
        else:
            logger.info(
                "Printing is disabled, sending all packets except the actual print command"
            )

        # send the first packet from our list, the packet handler will take care of the rest
        packet = self.packets_for_printing.pop(0)
        await self.send_packet(packet)

        with move_on_after(timeout):
            while len(self.packets_for_printing) > 0 or self.awaiting_response:
                await asleep(poll_delay)

    def print_services(self):
        """Display and overview of the printer's services and characteristics"""
        services = self.client.services
        service_characteristic_pair = []
        for service in services:
            for characteristic in service.characteristics():
                service_characteristic_pair.append(
                    (service.uuid(), characteristic.uuid())
                )

        for i, (service_uuid, characteristic) in enumerate(
            service_characteristic_pair
        ):
            logger.info(f"{i}: {service_uuid} {characteristic}")

    async def get_printer_orientation(self):
        """Get the current XYZ orientation of the printer"""
        packet = self.create_packet(EventType.XYZ_AXIS_INFO)
        await self.send_packet(packet)

    def pil_image_to_bytes(
        self, img: Image.Image, max_size_kb: int | None = None
    ) -> bytearray:
        """Convert a PIL image to a bytearray"""
        img_buffer = BytesIO()

        # Convert the image to RGB mode if it's in RGBA mode
        if img.mode == "RGBA":
            img = img.convert("RGB")

        # Resize the image to <imageSize> pixels
        img = img.resize(self.image_size, Image.Resampling.LANCZOS)

        def save_img_with_quality(quality):
            img_buffer.seek(0)
            img_buffer.truncate(0)
            img.save(img_buffer, format="JPEG", quality=quality)
            return img_buffer.tell() / 1024

        if max_size_kb is not None:
            low_quality, high_quality = 1, 100
            current_quality = 75
            closest_quality = current_quality
            min_target_size_kb = max_size_kb * 0.9

            while low_quality <= high_quality:
                output_size_kb = save_img_with_quality(current_quality)

                if (
                    output_size_kb <= max_size_kb
                    and output_size_kb >= min_target_size_kb
                ):
                    closest_quality = current_quality
                    break

                if output_size_kb > max_size_kb:
                    high_quality = current_quality - 1
                else:
                    low_quality = current_quality + 1

                current_quality = (low_quality + high_quality) // 2
                closest_quality = current_quality

            # Save the image with the closest_quality
            save_img_with_quality(closest_quality)
            logger.info(f"Saved img with quality of {closest_quality}")
        else:
            img.save(img_buffer, format="JPEG")

        return bytearray(img_buffer.getvalue())
