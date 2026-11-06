# -*- coding: utf-8 -*-
# I2C bus scanner for Tizen (python2.7, no external modules)
import os, sys, fcntl, errno

I2C_SLAVE = 0x0703
bus = int(sys.argv[1]) if len(sys.argv) > 1 else 1
fd = os.open("/dev/i2c-%d" % bus, os.O_RDWR)
found = []
for addr in range(0x03, 0x78):
    try:
        fcntl.ioctl(fd, I2C_SLAVE, addr)
        os.read(fd, 1)
        found.append(addr)
    except (IOError, OSError) as e:
        if e.errno not in (errno.ENXIO, errno.EREMOTEIO, errno.EIO, errno.ETIMEDOUT):
            print("0x%02x: %s" % (addr, e))
os.close(fd)
print("i2c-%d devices: %s" % (bus, " ".join("0x%02x" % a for a in found) or "(none)"))
