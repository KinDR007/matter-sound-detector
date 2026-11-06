#!/usr/bin/env python3
"""Send one shell command to the device and print the reply. Usage: dev_cmd.py "snd classes" [seconds]"""
import sys
import time

import serial
from serial.tools import list_ports

if len(sys.argv) < 2:
    sys.exit('usage: dev_cmd.py "command" [seconds]')
port = next((p.device for p in list_ports.comports() if p.vid == 0x2886), None)
if not port:
    sys.exit("device not found")
wait = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
with serial.Serial(port, 115200, timeout=0.2) as s:
    s.read(65536)
    for ch in sys.argv[1] + "\n":
        s.write(ch.encode())
        time.sleep(0.01)
    end, out = time.time() + wait, b""
    while time.time() < end:
        out += s.read(4096)
print(out.decode(errors="replace"))
