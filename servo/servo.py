# -*- coding: utf-8 -*-
"""
PCA9685 servo tester for Tizen 6.0 on Raspberry Pi (python2.7, no extra modules).

Usage (on the Pi):
  python servo.py center [ch]            # 1500us (중앙)
  python servo.py us <microsec> [ch]     # 원하는 펄스폭 (500~2500 로 제한)
  python servo.py angle <0-180> [ch]     # 각도 (기본 1000~2000us 매핑)
  python servo.py sweep [ch]             # 안전 범위 1000~2000us 왕복
  python servo.py wide [ch]              # 넓은 범위 600~2400us 왕복 (끝에서 소리나면 중단)
  python servo.py off [ch]               # 신호 끊기 (서보 힘 풀림)

옵션 환경변수: PCA_ADDR=0x40, I2C_BUS=1
"""
import os, sys, fcntl, time

I2C_SLAVE = 0x0703
MODE1, MODE2, PRESCALE, LED0_ON_L, ALL_LED_ON_L = 0x00, 0x01, 0xFE, 0x06, 0xFA
OSC_HZ = 25000000.0
FREQ = 50.0                    # 서보 표준 50Hz (20ms 주기)
US_MIN, US_MAX = 500, 2500     # 하드 리밋
SAFE_MIN, SAFE_MAX = 1000, 2000


class PCA9685(object):
    def __init__(self, bus=1, addr=0x40):
        self.fd = os.open("/dev/i2c-%d" % bus, os.O_RDWR)
        fcntl.ioctl(self.fd, I2C_SLAVE, addr)

    def w(self, *data):
        os.write(self.fd, bytes(bytearray(data)))

    def r(self, reg):
        self.w(reg)
        return bytearray(os.read(self.fd, 1))[0]

    def init(self, freq=FREQ):
        prescale = int(round(OSC_HZ / (4096.0 * freq)) - 1)
        self.w(ALL_LED_ON_L, 0, 0, 0, 0x10)      # 전체 채널 OFF (MODE1 AI 전이라 개별 기록)
        self.w(MODE2, 0x04)                      # OUTDRV: totem-pole
        self.w(MODE1, 0x10)                      # SLEEP (prescale 변경은 sleep 중에만)
        self.w(PRESCALE, prescale)
        self.w(MODE1, 0x20)                      # wake + auto-increment
        time.sleep(0.005)
        self.period_us = 1e6 * (prescale + 1) * 4096.0 / OSC_HZ
        return prescale

    def raw(self, ch, on, off):
        self.w(LED0_ON_L + 4 * ch, on & 0xFF, on >> 8, off & 0xFF, off >> 8)

    def pulse_us(self, ch, us):
        us = max(US_MIN, min(US_MAX, us))
        self.raw(ch, 0, int(round(us * 4096.0 / self.period_us)))
        return us

    def off(self, ch):
        self.raw(ch, 0, 0x1000)                  # full-off bit


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    cmd = a[0]
    addr = int(os.environ.get("PCA_ADDR", "0x40"), 0)
    p = PCA9685(int(os.environ.get("I2C_BUS", "1")), addr)
    try:
        mode1 = p.r(MODE1)
    except (IOError, OSError) as e:
        print("PCA9685(0x%02x) 응답 없음: %s  -> 배선/전원 확인" % (addr, e)); sys.exit(1)
    if cmd != "off" and (mode1 & 0x10 or p.r(PRESCALE) != int(round(OSC_HZ / (4096.0 * FREQ)) - 1)):
        print("init: prescale=%d (50Hz)" % p.init())
    else:
        p.period_us = 1e6 * (p.r(PRESCALE) + 1) * 4096.0 / OSC_HZ

    def ch_arg(i):
        return int(a[i]) if len(a) > i else 0

    if cmd == "center":
        ch = ch_arg(1); print("ch%d -> %dus" % (ch, p.pulse_us(ch, 1500)))
    elif cmd == "us":
        ch = ch_arg(2); print("ch%d -> %dus" % (ch, p.pulse_us(ch, int(a[1]))))
    elif cmd == "angle":
        ch = ch_arg(2); deg = max(0.0, min(180.0, float(a[1])))
        us = SAFE_MIN + (SAFE_MAX - SAFE_MIN) * deg / 180.0
        print("ch%d %.0fdeg -> %dus" % (ch, deg, p.pulse_us(ch, us)))
    elif cmd in ("sweep", "wide"):
        ch = ch_arg(1)
        lo, hi = (SAFE_MIN, SAFE_MAX) if cmd == "sweep" else (600, 2400)
        for us in (1500, lo, 1500, hi, 1500):
            print("ch%d -> %dus" % (ch, p.pulse_us(ch, us))); time.sleep(1.2)
        print("done (1500us 유지 중, 풀려면: off)")
    elif cmd == "off":
        ch = ch_arg(1); p.off(ch); print("ch%d off" % ch)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
