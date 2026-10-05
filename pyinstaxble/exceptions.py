
class PrinterTimeoutError(BaseException):
  def __init__(self, timeout):
    self.timeout = timeout