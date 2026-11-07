"""라즈베리파이(Tizen 6.0) 노브 웹 앱 묶기.

    uv run python tools/build_knob_wgt.py                          # 맥의 현재 Wi-Fi IP를 기본 서버 주소로
    uv run python tools/build_knob_wgt.py 10.10.100.41             # 주소 지정
    uv run python tools/build_knob_wgt.py --install                # 서명 → 라즈베리파이 설치 → 실행

출력: knob/dist/PalmKnob01/ (앱 폴더)와 knob/dist/PalmKnob.wgt
Tizen은 서명된 앱만 설치한다. Tizen Studio CLI(`tizen`)와 보안 프로필이 있으면 --install 이 서명까지 한다
(knob/README.md 참고). 앱 안의 서버 주소는 노브 화면 위쪽 상태 줄을 길게 눌러 언제든 바꿀 수 있다.

환경 변수: PALMWEB_PI (기본 10.10.101.193:26101), PALMWEB_TIZEN_PROFILE (기본 palm)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
KNOB = ROOT / "knob"
OUT = KNOB / "dist"
APP = OUT / "PalmKnob01"
PORT = 8000
PI = os.environ.get("PALMWEB_PI", "10.10.101.193:26101")
PROFILE = os.environ.get("PALMWEB_TIZEN_PROFILE", "palm")
APP_ID = "PalmKnob01.knob"


def find_tool(name: str, candidates: list[Path]) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if out:
        print("\n".join(out.splitlines()[-12:]))
    return r


def install(wgt_dir: Path):
    home = Path.home()
    tizen = find_tool("tizen", [home / "tizen-studio/tools/ide/bin/tizen"])
    sdb = find_tool("sdb", [home / "tizen-sdb/sdb", home / "tizen-studio/tools/sdb"])
    if not sdb:
        raise SystemExit("sdb를 찾지 못했습니다.")
    if not tizen:
        raise SystemExit("Tizen Studio CLI(tizen)가 없어 서명할 수 없습니다. knob/README.md 의 '서명 준비'를 먼저 하세요.")
    # 1) 서명: 앱 폴더 안에 서명 파일을 만들고 .wgt 로 묶는다
    r = run([tizen, "package", "-t", "wgt", "-s", PROFILE, "--", str(wgt_dir)])
    signed = sorted(wgt_dir.glob("*.wgt"), key=lambda p: p.stat().st_mtime)
    if r.returncode != 0 or not signed:
        raise SystemExit(f"서명 실패. 보안 프로필 '{PROFILE}'이 있는지 확인하세요: tizen security-profiles list")
    # 2) 설치와 3) 실행
    run([sdb, "connect", PI])
    r = run([sdb, "-s", PI, "install", str(signed[-1])])
    if "val[ok]" not in (r.stdout + r.stderr) and r.returncode != 0:
        raise SystemExit("설치 실패")
    run([sdb, "-s", PI, "shell", f"app_launcher -s {APP_ID}"])
    print("라즈베리파이 화면에 노브가 떴는지 확인하세요.")


def mac_ip() -> str:
    for iface in ("en0", "en1"):
        r = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True)
        if r.stdout.strip():
            return r.stdout.strip()
    raise SystemExit("맥의 Wi-Fi IP를 찾지 못했습니다. 주소를 인자로 주세요.")


def icon(path: Path, size: int = 117):
    im = Image.new("RGBA", (size, size), (15, 14, 13, 255))
    d = ImageDraw.Draw(im)
    m = 14
    d.arc([m, m, size - m, size - m], start=135, end=405, fill=(60, 56, 52, 255), width=12)
    d.arc([m, m, size - m, size - m], start=135, end=330, fill=(255, 159, 69, 255), width=12)
    d.ellipse([size / 2 - 9, size / 2 - 9, size / 2 + 9, size / 2 + 9], fill=(236, 233, 228, 255))
    im.save(path)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    server = args[0] if args else mac_ip()
    if ":" not in server:
        server = f"{server}:{PORT}"
    if APP.exists():
        shutil.rmtree(APP)
    APP.mkdir(parents=True)
    html = (KNOB / "index.html").read_text(encoding="utf-8").replace("__DEFAULT_SERVER__", server)
    (APP / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(KNOB / "tizen" / "config.xml", APP / "config.xml")
    icon(APP / "icon.png")
    wgt = OUT / "PalmKnob.wgt"
    with zipfile.ZipFile(wgt, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(APP.iterdir()):
            z.write(f, f.name)
    print(f"기본 서버 주소: {server}")
    print(f"앱 폴더: {APP}")
    print(f"묶음(서명 전): {wgt}")
    if "--install" in sys.argv:
        install(APP)


if __name__ == "__main__":
    main()
