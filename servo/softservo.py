# -*- coding: utf-8 -*-
"""
Direct-GPIO software PWM servo tester (Tizen 6.0 / Raspberry Pi 4, python2.7).
Signal: GPIO18 (header pin 12). 50Hz, pulse width in microseconds.

Usage (root, on the Pi):
  python softservo.py state                 # GPIO 기능/레벨 확인만
  python softservo.py us <microsec> [sec]   # 해당 펄스 유지 (기본 2초)
  python softservo.py center [sec]          # 1500us
  python softservo.py test                  # 1500 -> 1250 -> 1500 -> 1750 -> 1500 (작은 범위)
  python softservo.py sweep                 # 1000 -> 2000 왕복
  python softservo.py angle <0-180> [sec]   # 1000~2000us 매핑
끝나면 신호를 끊고(LOW) 핀을 입력으로 되돌립니다 -> 서보 힘이 풀립니다.
"""
import os, sys, mmap, struct, time

PIN = int(os.environ.get("SERVO_GPIO", "18"))
PERIOD = 0.020
US_MIN, US_MAX = 500, 2500
GPFSEL, GPSET0, GPCLR0, GPLEV0 = 0x00, 0x1C, 0x28, 0x34


class Gpio(object):
    def __init__(self):
        self.fd = os.open("/dev/gpiomem", os.O_RDWR | os.O_SYNC)
        self.m = mmap.mmap(self.fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)

    def rd(self, off):
        return struct.unpack("<I", self.m[off:off + 4])[0]

    def wr(self, off, v):
        self.m[off:off + 4] = struct.pack("<I", v & 0xFFFFFFFF)

    def func(self, pin):
        return (self.rd(GPFSEL + 4 * (pin // 10)) >> (3 * (pin % 10))) & 7

    def set_func(self, pin, f):
        off, sh = GPFSEL + 4 * (pin // 10), 3 * (pin % 10)
        self.wr(off, (self.rd(off) & ~(7 << sh)) | (f << sh))

    def level(self, pin):
        return (self.rd(GPLEV0) >> pin) & 1


def run(g, plan):
    """plan: [(us, seconds), ...]"""
    setr, clrr, bit = GPSET0, GPCLR0, struct.pack("<I", 1 << PIN)
    m, now = g.m, time.time
    orig = g.func(PIN)
    g.m[clrr:clrr + 4] = bit
    g.set_func(PIN, 1)  # output
    try:
        for us, sec in plan:
            us = max(US_MIN, min(US_MAX, us))
            print("GPIO%d -> %dus  (%.1fs)" % (PIN, us, sec)); sys.stdout.flush()
            hi = us / 1e6
            t_end = now() + sec
            t0 = now()
            late = 0
            errs = []
            while t0 < t_end:
                ts = now()
                m[setr:setr + 4] = bit
                t1 = ts + hi
                while now() < t1:
                    pass
                m[clrr:clrr + 4] = bit
                errs.append((now() - ts - hi) * 1e6)
                nxt = t0 + PERIOD
                rem = nxt - now() - 0.0015
                if rem > 0:
                    time.sleep(rem)
                while now() < nxt:
                    pass
                t0 = nxt
                if now() - t0 > 0.002:  # 스케줄 밀림 -> 재동기
                    late += 1
                    t0 = now()
            if late:
                print("   (timing slips: %d)" % late)
            if errs:
                s = sorted(errs)
                big = sum(1 for e in errs if e > 20)
                print("   pulses=%d  err avg=%.1fus p99=%.1fus max=%.1fus  >20us: %d"
                      % (len(s), sum(s) / len(s), s[int(len(s) * 0.99) - 1], s[-1], big))
    finally:
        g.m[clrr:clrr + 4] = bit
        restore = orig if orig != 1 else 0
        g.set_func(PIN, restore)  # 원래 기능 복원 (출력이었으면 입력으로)
        print("signal off, GPIO%d func restored -> %d" % (PIN, restore))


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    try:
        os.nice(-20)
    except OSError:
        pass
    if os.environ.get("RT", "1") == "1":
        try:  # SCHED_FIFO 실시간 우선순위 + 메모리 고정 -> 선점으로 인한 펄스 튐 감소
            import ctypes
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            prio = ctypes.c_int(80)
            if libc.sched_setscheduler(0, 1, ctypes.byref(prio)) != 0:
                print("RT sched 실패 errno=%d" % ctypes.get_errno())
            libc.mlockall(3)
        except Exception as e:
            print("RT sched 실패: %s" % e)
    g = Gpio()
    cmd = a[0]
    if cmd == "state":
        print("GPIO%d func=%d (0=in,1=out) level=%d" % (PIN, g.func(PIN), g.level(PIN))); return
    if g.func(PIN) not in (0, 1) and os.environ.get("FORCE") != "1":
        print("GPIO%d 가 다른 기능(%d)으로 사용 중 -> 중단 (FORCE=1 로 임시 사용 후 복원)" % (PIN, g.func(PIN))); sys.exit(1)
    sec = lambda i, d: float(a[i]) if len(a) > i else d
    if cmd == "us":
        plan = [(int(a[1]), sec(2, 2.0))]
    elif cmd == "center":
        plan = [(1500, sec(1, 2.0))]
    elif cmd == "angle":
        deg = max(0.0, min(180.0, float(a[1])))
        plan = [(int(1000 + 1000 * deg / 180.0), sec(2, 2.0))]
    elif cmd == "test":
        plan = [(1500, 1.5), (1250, 1.2), (1500, 1.2), (1750, 1.2), (1500, 1.2)]
    elif cmd == "sweep":
        plan = [(1500, 1.2), (1000, 1.5), (1500, 1.2), (2000, 1.5), (1500, 1.2)]
    else:
        print(__doc__); return
    run(g, plan)


if __name__ == "__main__":
    main()
