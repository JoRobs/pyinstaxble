import argparse
import logging
import sys
from io import BytesIO
from math import ceil
from struct import pack, unpack_from
from time import sleep
from uuid import UUID

import anyio
from bleak import BleakScanner, BleakClient, BLEDevice
from PIL import Image

import pyinstaxble.led_patterns as LedPatterns
from pyinstaxble.instax_types import EventType, InfoType, PrinterSettingsData, PrinterSettings

logger = logging.getLogger(__name__)

SERVICE_UUID = UUID("70954782-2d83-473d-9e5f-81e1d02d5273")
WRITECHAR_UUID = UUID("70954783-2d83-473d-9e5f-81e1d02d5273")
NOTIFYCHAR_UUID = UUID("70954784-2d83-473d-9e5f-81e1d02d5273")

class InstaxBLEAK:
    printer_settings: PrinterSettingsData
    device_address: str | None
    device_name: str | None
    print_enabled: bool
    client: BleakClient | None = None

    def __init__(
        self,
        printer_settings:PrinterSettingsData | None=PrinterSettings.MINI,
        device_address:str=None,
        device_name:str=None,
        print_enabled:bool=False,
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
        self.device_address: str = device_address.upper() if device_address else None
        self.packets_for_printing: list = []
        self.pos = (0, 0, 0, 0)
        self.battery_state = 0
        self.battery_percentage = 0
        self.photos_left = 0
        self.is_charging = False
        self.image_size = (printer_settings.width, printer_settings.height)
        self.waitingForResponse = False
        self.cancelled = False
        self.scanner = BleakScanner(self.detection_callback)

    def detection_callback(self, device, data)->None:
        if not data.local_name and device.name:
            logger.debug("Detected BLE advertisement, but not enough data to log")
            return

        logger.debug(f"Device {device.name} detected BLE advertisement {data.local_name}")

    def log(self, msg):
        """Print a debug message"""
        logger.info(msg)

    def display_current_status(self):
        """Display an overview of the current printer state"""
        print("\nPrinter details: ")
        # print(f"Device name:         {self.printer_settings['modelName']}")
        print(f"Model:               {self.printer_settings['modelName']}")
        print(f"Photos left:         {self.photos_left}/10")
        print(f"Battery level:       {self.battery_percentage}%")
        print(f"Charging:            {self.is_charging}")
        print(
            f"Required image size: {self.printer_settings['width']}x{self.printer_settings['height']}px"
        )
        if self.peripheral.mtu:
            print(f"MTU:                 {self.peripheral.mtu()}")
        print()

    # TODO: Update to bleak
    def parse_printer_response(self, event, packet):
        """Parse the response packet and print the result"""
        # self.log(f"event: {event}")
        self.waitingForResponse = False

        if event == EventType.XYZ_AXIS_INFO:
            x, y, z, o = unpack_from("<hhhB", packet[6:-1])
            self.pos = (x, y, z, o)
        elif event == EventType.LED_PATTERN_SETTINGS:
            pass
        elif event == EventType.SUPPORT_FUNCTION_INFO:
            try:
                infoType = InfoType(packet[7])
            except ValueError:
                self.log(f"Unknown InfoType: {packet[7]}")
                return

            if infoType == InfoType.IMAGE_SUPPORT_INFO:
                w, h = unpack_from(">HH", packet[8:12])
                # self.log(self.prettify_bytearray(packet[8:12]))
                # self.log(f'image size: {w}x{h}')
                self.image_size = (w, h)
                if (w, h) == (600, 800):
                    self.printer_settings = PrinterSettings["mini"]
                elif (w, h) == (800, 800):
                    self.printer_settings = PrinterSettings["square"]
                elif (w, h) == (1260, 840):
                    self.printer_settings = PrinterSettings["wide"]
                else:
                    sys.exit(f"Unknown image size from printer: {w}x{h}")

                self.chunk_size = self.printer_settings["chunkSize"]

            elif infoType == InfoType.BATTERY_INFO:
                self.battery_state, self.battery_percentage = unpack_from(
                    ">BB", packet[8:10]
                )
                # self.log(f'battery state: {self.batteryState}, battery percentage: {self.batteryPercentage}')
            elif infoType == InfoType.PRINTER_FUNCTION_INFO:
                dataByte = packet[8]
                self.photos_left = dataByte & 15
                self.is_charging = (1 << 7) & dataByte >= 1
                # self.log(f'photos left: {self.photosLeft}')
                # if self.isCharging:
                #     self.log('Printer is charging')
                # else:
                #     self.log('Printer is running on battery')

        elif (
            event == EventType.PRINT_IMAGE_DOWNLOAD_START
            or event == EventType.PRINT_IMAGE_DOWNLOAD_DATA
            or event == EventType.PRINT_IMAGE_DOWNLOAD_END
        ):
            self.handle_image_packet_queue()

        elif event == EventType.PRINT_IMAGE_DOWNLOAD_CANCEL:
            pass

        elif event == EventType.PRINT_IMAGE:
            self.log("received print confirmation")

        else:
            self.log(f"Uncaught response from printer. Eventype: {event}")

    # TODO: Update to bleak
    async def handle_image_packet_queue(self):
        if len(self.packets_for_printing) > 0 and not self.cancelled:
            if len(self.packets_for_printing) % 10 == 0:
                self.log(
                    f"Img packets left to send: {len(self.packets_for_printing)}"
                )
            packet = self.packets_for_printing.pop(0)
            await self.send_packet(packet)

    async def notification_handler(self, char_uuid, packet)->None:
        """Gets called whenever the printer replies and handles parsing the received data"""
        logger.debug(f"Char: {char_uuid}")
        logger.debug(f"Bytes: {packet}")

        if len(packet) < 8:
            self.log(
                f"\tError: response packet size should be >= 8 (was {len(packet)})!"
            )
            return
        elif not self.validate_checksum(packet):
            self.log("\tResponse packet checksum was invalid!")
            return

        _header, _length, op1, op2 = unpack_from(">HHBB", packet)
        # self.log('\theader: ', header, '\t', self.prettify_bytearray(packet[0:2]))
        # self.log('\tlength: ', length, '\t', self.prettify_bytearray(packet[2:4]))
        # self.log('\top1: ', op1, '\t\t', self.prettify_bytearray(packet[4:5]))
        # self.log('\top2: ', op2, '\t\t', self.prettify_bytearray(packet[5:6]))

        try:
            event = EventType((op1, op2))
            # self.log(f'\tResponse event: {event}')
        except ValueError:
            self.log(f"Unknown EventType: ({op1}, {op2})")
            return

        self.parse_printer_response(event, packet)

    async def find_device(self, timeout=5) -> BLEDevice | None:
            """ " Scan for our device and return it when found"""
            logger.debug("Searching for instax printer...")

            try:
                devices: list[BLEDevice] = await self.scanner.discover(timeout)
                for device in devices:

                    if (self.device_name
                        and device.name
                        and device.name.startswith(self.device_name)):
                        return device

                    if (self.device_address
                        and device.address
                        and device.address.startswith(self.device_address)):
                        return device

                    if (device.name
                        and device.name.startswith("INSTAX-")
                        and device.name.endswith("(BLE)")):
                        return device

                logger.debug("No devices found")
                return None

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
                await self.client.start_notify( 17, callback=self.notification_handler)
            except Exception as e:
                logger.error(f"Error on attaching notification_handler: {e}")
                return

            await self.get_printer_info()
        else:
            logger.debug("No connectable device found.")


    # TODO: Update to bleak
    def disconnect(self):
        """Disconnect from the printer (if connected)"""
        if self.dummyPrinter:
            return
        if self.peripheral and self.peripheral.is_connected():
            # if len(self.packetsForPrinting) > 0 and not self.cancelled:
            #     self.log('sending cancel command')
            #     await self.send_packet(self.create_packet(EventType.PRINT_IMAGE_DOWNLOAD_CANCEL))
            self.log("Disconnecting...")
            self.peripheral.disconnect()
            self.log("Disconnected")

    # TODO: Update to bleak
    async def cancel_print(self):
        self.packets_for_printing = []
        self.waitingForResponse = False
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

    # TODO: Update to bleak
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

    async def send_packet(self, packet):
        """Send a packet to the printer"""

        if not self.client:
            logger.error("No connected device, run connect first.")
            return

        try:
            while (
                self.waitingForResponse
                and not self.cancelled
            ):
                # self.log("sleep")
                sleep(0.05)

            _header, _length, op1, op2 = unpack_from(">HHBB", packet)
            try:
                EventType((op1, op2))
            except Exception as e:
                logger.error(e.with_traceback())

            self.waitingForResponse = True
            smallPacketSize = 182
            numberOfParts = ceil(len(packet) / smallPacketSize)
            # self.log(f"> number of parts to send: {numberOfParts}")
            for subPartIndex in range(numberOfParts):
                # self.log((subPartIndex + 1), '/', numberOfParts)
                subPacket = packet[
                    subPartIndex * smallPacketSize : subPartIndex
                    * smallPacketSize
                    + smallPacketSize
                ]

                await self.client.write_gatt_char(WRITECHAR_UUID, subPacket)

        except KeyboardInterrupt:
            self.cancelled = True
            self.cancel_print()
            # sleep(1)
            self.disconnect()
            sys.exit("Cancelled")

    # TODO: Update to bleak
    async def print_image(self, imgSrc):
        """
        print an image. Either pass a path to an image (as a string) or pass
        the bytearray to print directly
        """
        self.log(f'printing image "{imgSrc}"')
        if self.photos_left == 0 and not self.dummyPrinter:
            self.log("Can't print: no photos left")
            return

        imgData = imgSrc
        if isinstance(imgSrc, str):  # if it's a path, load the image contents
            image = Image.open(imgSrc)
            imgData = self.pil_image_to_bytes(image, max_size_kb=105)
        elif isinstance(imgSrc, BytesIO):
            imgSrc.seek(0)  # Go to the start of the BytesIO object
            image = Image.open(imgSrc)
            imgData = self.pil_image_to_bytes(image, max_size_kb=105)

        # self.log(f"len of imagedata: {len(imgData)}")
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
            self.packets_for_printing.append(self.create_packet((0, 2), b"\x02"))
        else:
            self.log(
                "Printing is disabled, sending all packets except the actual print command"
            )

        # for packet in self.packetsForPrinting:
        #     self.log(self.prettify_bytearray(packet))
        # exit()
        # send the first packet from our list, the packet handler will take care of the rest
        if not self.dummyPrinter:
            packet = self.packets_for_printing.pop(0)
            await self.send_packet(packet)
            # try:
            #     while len(self.packetsForPrinting) > 0:
            #         sleep(0.1)
            # except KeyboardInterrupt:
            #     self.cancelled = True
            #     self.disconnect()
            #     sys.exit('Cancelled')

    # TODO: Update to bleak
    def print_services(self):
        """Get and display and overview of the printer's services and characteristics"""
        self.log("Successfully connected, listing services...")
        services = self.peripheral.services()
        service_characteristic_pair = []
        for service in services:
            for characteristic in service.characteristics():
                service_characteristic_pair.append(
                    (service.uuid(), characteristic.uuid())
                )

        for i, (service_uuid, characteristic) in enumerate(
            service_characteristic_pair
        ):
            self.log(f"{i}: {service_uuid} {characteristic}")

    async def get_printer_orientation(self):
        """Get the current XYZ orientation of the printer"""
        packet = self.create_packet(EventType.XYZ_AXIS_INFO)
        await self.send_packet(packet)

    async def get_printer_info(self):
        """Get and display the printer's status and info, like photos left and battery level"""
        # self.log("Getting function info...")

        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.IMAGE_SUPPORT_INFO.value),
        )
        await self.send_packet(packet)

        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.BATTERY_INFO.value),
        )
        await self.send_packet(packet)

        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.PRINTER_FUNCTION_INFO.value),
        )
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
                # self.log(f"current output quality: {current_quality}, current size: {output_size_kb}")

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
            self.log(f"Saved img with quality of {closest_quality}")
        else:
            img.save(img_buffer, format="JPEG")

        return bytearray(img_buffer.getvalue())

    # TODO: Update to bleak
    def wait_one_minute(self):
        """Wait for one minute. Hacky way of preventing disconnecting too soon"""
        self.log("Waiting for one minute...")
        sleep(60)


# TODO: Update to bleak
def main(args=None):
    """Example usage of the InstaxBLE class"""
    if args is None:
        args = {}
    instax = InstaxBLEAK(**args)
    try:
        # To prevent misprints during development this script sends all the
        # image data except the final 'go print' command. To enable printing
        # uncomment the next line, or pass --print-enabled when calling
        # this script

        # instax.enable_printing()
        instax.connect()
        # Set a rainbow effect to be shown while printing and a pulsating
        # green effect when printing is done
        instax.send_led_pattern(LedPatterns.rainbow, when=1)
        instax.send_led_pattern(LedPatterns.pulseGreen, when=2)
        # you can also read the current accelerometer values if you want
        # while True:
        #     instax.get_printer_orientation()
        #     sleep(.5)

        # send your image (.jpg) to the printer by
        # passing the image_path as an argument when calling
        # this script, or by specifying the path in your code
        if instax.image_path:
            instax.print_image(instax.image_path)
        else:
            instax.print_image(instax.printerSettings["exampleImage"])
        instax.wait_one_minute()

    except Exception as e:
        print(type(e).__name__, __file__, e.__traceback__.tb_lineno)
        instax.log(f"Error: {e}")
    finally:
        print("finally, disconnect")
        instax.disconnect()  # all done, disconnect


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--device-address")
    parser.add_argument("-n", "--device-name")
    parser.add_argument("-p", "--print-enabled", action="store_true")
    parser.add_argument("-d", "--dummy-printer", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-i", "--image-path", help="Path to the image file")
    args = parser.parse_args()

    main(vars(args))
