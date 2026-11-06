# -*- coding: utf-8 -*-
"""
Relay module tester (Tizen 6.0 / Raspberry Pi 4, python2.7).
Signal: GPIO17 (header pin 11) by default.

Usage (root, on the Pi):
  python relay.py state                  # 핀 상태 확인
  python relay.py on                     # 릴레이 ON 유지 (모터 돔)
  python relay.py off                    # 릴레이 OFF 유지 (모터 멈춤)
  python relay.py pulse [sec]            # sec 초 동안 ON 후 OFF (기본 1초)
  python relay.py blink [n] [on] [off]   # n 회 ON/OFF 반복 (기본 3회, 1초/1초)
  python relay.py release                # 핀을 입력으로 (신호 해제)

환경변수:
  RELAY_GPIO=17        신호 핀 (BCM 번호)
  ACTIVE_LOW=1         대부분의 릴레이 모듈은 LOW 에서 ON (반대면 ACTIVE_LOW=0)
"""
import os, sys, mmap, struct, time

PIN = int(os.environ.get("RELAY_GPIO", "17"))
ACTIVE_LOW = os.environ.get("ACTIVE_LOW", "1") == "1"
GPFSEL, GPSET0, GPCLR0, GPLEV0 = 0x00, 0x1C, 0x28, 0x34


class Gpio(object):
    def __init__(self):
        self.fd = os.open("/dev/gpiomem", os.O_RDWR | os.O_SYNC)
        self.m = mmap.mmap(self.fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)

    def rd(self, off):
        return struct.unpack("<I", self.m[off:off + 4])[0]

    def wr(self, off, v):
        self.m[off:off + 4] = struct.pack("<I", v & 0xFFFFFFFF)

    def func(self):
        return (self.rd(GPFSEL + 4 * (PIN // 10)) >> (3 * (PIN % 10))) & 7

    def set_func(self, f):
        off, sh = GPFSEL + 4 * (PIN // 10), 3 * (PIN % 10)
        self.wr(off, (self.rd(off) & ~(7 << sh)) | (f << sh))

    def level(self):
        return (self.rd(GPLEV0) >> PIN) & 1

    def write(self, high):
        self.wr(GPSET0 if high else GPCLR0, 1 << PIN)

    def relay(self, on):
        # 출력 방향 전환 전에 레벨부터 맞춰서 순간 오동작 방지
        self.write((not on) if ACTIVE_LOW else on)
        if self.func() != 1:
            self.set_func(1)
        print("relay %s  (GPIO%d=%d)" % ("ON " if on else "OFF", PIN, self.level()))
        sys.stdout.flush()


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    g = Gpio()
    f = g.func()
    if f not in (0, 1):
        print("GPIO%d 가 다른 기능(%d)으로 사용 중 -> 중단" % (PIN, f)); sys.exit(1)
    cmd = a[0]
    num = lambda i, d: float(a[i]) if len(a) > i else d
    if cmd == "state":
        print("GPIO%d func=%d (0=in,1=out) level=%d  active_low=%s" % (PIN, f, g.level(), ACTIVE_LOW))
    elif cmd == "on":
        g.relay(True)
    elif cmd == "off":
        g.relay(False)
    elif cmd == "pulse":
        try:
            g.relay(True); time.sleep(num(1, 1.0))
        finally:
            g.relay(False)
    elif cmd == "blink":
        n, t_on, t_off = int(num(1, 3)), num(2, 1.0), num(3, 1.0)
        try:
            for i in range(n):
                g.relay(True); time.sleep(t_on)
                g.relay(False); time.sleep(t_off)
        finally:
            g.relay(False)
    elif cmd == "release":
        g.set_func(0); print("GPIO%d -> input" % PIN)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
