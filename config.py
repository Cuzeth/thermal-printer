"""Printer configuration. Adjust these for your specific hardware."""

# USB IDs for the thermal printer (from test_print.py)
USB_VENDOR_ID = 0x0483
USB_PRODUCT_ID = 0x5720
USB_OUT_EP = 0x03
USB_IN_EP = 0x81

# Receipt width in characters at standard font. 32 for 58mm, 42 for 80mm.
RECEIPT_WIDTH = 32

# Printer raster image width in pixels (typical 58mm = 384px, 80mm = 576px)
PRINTER_PIXEL_WIDTH = 384

# Set to True to skip real printing and just write the ESC/POS bytes to a file
# (useful for debugging without wasting paper).
DRY_RUN = False
DRY_RUN_PATH = "last_print.bin"

# Host/port for the web UI
HOST = "127.0.0.1"
PORT = 5005
