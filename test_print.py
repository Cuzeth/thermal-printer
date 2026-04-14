from escpos.printer import Usb

p = Usb(0x0483, 0x5720, out_ep=0x03, in_ep=0x81)

p.set(align="center", bold=True, double_height=True, double_width=True)
p.text("IT WORKS\n")
p.set(align="center", bold=False, double_height=False, double_width=False)
p.text("--------------------------------\n")
p.text("First print from Python + macOS\n")
p.text("--------------------------------\n")
p.qr("https://github.com", size=6, center=True)
p.text("\n")
p.cut()
