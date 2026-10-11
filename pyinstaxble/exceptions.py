class PrinterTimeoutError(BaseException):
    """Method timed out"""

    def __init__(self, timeout):
        self.timeout = timeout


class PrinterOutOfFilmError(BaseException):
    """Printer is out of film"""


class PrinterNotConnectedError(BaseException):
    """Printer is not connected"""
