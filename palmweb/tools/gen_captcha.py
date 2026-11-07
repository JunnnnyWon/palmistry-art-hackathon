"""예술 캡차용 이미지 30장 생성: "이 중 손이 아닌 것을 고르시오".

    uv run python tools/gen_captcha.py                 # 30장 모두 (web/public/captcha/hand-01.png … other-15.png + manifest.json + _sheet.jpg)
    uv run python tools/gen_captcha.py hand-04 other-11 # 특정 장만 다시 뽑기
    CAPTCHA_MODEL=bytedance-seed/seedream-5-0-flash CAPTCHA_OUT=web/public/captcha-seedream uv run python tools/gen_captcha.py   # 다른 모델로 비교

OpenRouter 이미지 엔드포인트(/api/v1/images/generations). 기본 모델 openai/gpt-image-2.6-sunburst (한 장 약 0.025달러).
키는 저장소 최상위 .env 의 OPENROUTER_API_KEY. 모든 장에 같은 접두 문장(검은 배경, 정면 스튜디오 조명, 사진 질감, 정사각형)을 붙여 톤을 맞춘다.

구성:
  hand-01 ~ hand-15   정상적인 손. 나이·피부색·자세를 다양하게
  other-01 ~ other-15 손이 아닌 것 같은 이미지. 손이지만 손 같지 않은 것(핏줄·여섯 손가락·X선·마네킹·로봇)부터
                      손이 아니지만 손 같은 것(나뭇가지·불가사리·그림자·장갑·뿌리·산호·잎)까지 수위를 섞는다
"""
import base64, json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / (os.environ.get("CAPTCHA_OUT") or "web/public/captcha")
MODEL = os.environ.get("CAPTCHA_MODEL") or "openai/gpt-image-2.6-sunburst"
STYLE = ("Photograph, square format, centered subject on a pure black background, single soft frontal studio light, "
         "neutral color grading, high detail, no text, no watermark. ")
HAND = [
    ("손바닥을 편 손", "An open adult human right hand, palm facing the camera, fingers relaxed and slightly spread, wrist at the bottom edge."),
    ("주먹", "A clenched adult human fist seen from the front, knuckles toward the camera, wrist at the bottom edge."),
    ("노인의 손", "An elderly person's open hand, palm facing the camera, deep wrinkles, prominent veins and age spots, wrist at the bottom edge."),
    ("손등", "The back of an adult human hand, fingers together and slightly curved, knuckles and tendons visible, wrist at the bottom edge."),
    ("아이의 손", "A young child's small open hand, palm facing the camera, soft chubby fingers, wrist at the bottom edge."),
    ("어두운 피부의 손", "An open hand with dark brown skin, palm facing the camera, fingers gently spread, wrist at the bottom edge."),
    ("손가락 하나", "An adult human hand pointing upward with the index finger, other fingers folded, seen from the front."),
    ("브이", "An adult human hand making a V sign with index and middle finger, palm toward the camera."),
    ("오케이", "An adult human hand making an OK sign, thumb and index finger forming a circle, other fingers raised."),
    ("반쯤 쥔 손", "An adult human hand half closed, fingers loosely curled as if about to hold something, seen from the front."),
    ("젖은 손", "An open adult human hand, palm facing the camera, skin wet with water droplets, wrist at the bottom edge."),
    ("반지 낀 손", "An open adult human hand, palm facing the camera, a plain silver ring on the ring finger, wrist at the bottom edge."),
    ("손가락 벌린 손", "An open adult human hand with fingers spread as wide as possible, palm facing the camera, wrist at the bottom edge."),
    ("옆에서 본 손", "An open adult human hand seen from the side, thumb toward the camera, fingers extended, wrist at the bottom edge."),
    ("두 손을 모은 모양", "Two adult human hands pressed together palm to palm in a prayer gesture, fingers pointing up, seen from the front."),
]
OTHER = [
    ("핏줄이 덮은 손", "An open human hand, palm facing the camera, but the skin is almost entirely covered by a dense web of dark red veins and thin root-like vessels branching out from the palm lines across the fingers, slightly translucent skin, unsettling medical look."),
    ("손가락 여섯 개", "An open human hand, palm facing the camera, that has exactly six fingers: five fingers plus one extra finger between the ring finger and the little finger, all realistic skin, otherwise ordinary."),
    ("X선 손", "A medical X-ray radiograph of a human hand, palm flat, bones glowing white against black, soft grey outline of flesh visible."),
    ("마네킹 손", "A glossy white plastic mannequin hand, open, palm facing the camera, visible seam lines at the joints, wrist at the bottom edge."),
    ("로봇 손", "A robotic humanoid hand made of brushed metal and black joints, open, palm facing the camera, wrist at the bottom edge."),
    ("유리 손", "A sculpture of an open hand made of clear glass, palm facing the camera, light refracting through it, wrist at the bottom edge."),
    ("석고상 손", "An open hand carved in white marble, a fragment of a classical statue, palm facing the camera, slightly chipped, standing on a cut wrist."),
    ("손가락이 너무 긴 손", "An open human hand, palm facing the camera, with fingers that are unnaturally long, each finger twice the normal length, realistic skin, wrist at the bottom edge."),
    ("다섯 갈래 나뭇가지", "A dry bare tree branch that splits into five finger-like twigs so that it resembles an open hand, bark texture, held upright."),
    ("불가사리", "A single beige starfish standing upright on the tips of its five arms so that its silhouette resembles a spread hand, seen from the front."),
    ("손 그림자", "Only the shadow of an open hand cast on a very dark charcoal wall lit by one dim spotlight, the shadow a deeper black, the hand itself outside the frame and not visible, the wall fading into pure black at the edges so the frame matches a black background."),
    ("빈 장갑", "An empty beige latex glove standing upright and inflated as if a hand were inside, fingers spread, but clearly hollow, wrist at the bottom edge."),
    ("인삼 뿌리", "A ginseng root with five thick forked rootlets spread like fingers so that it resembles a hand, pale tan color, held upright."),
    ("산호", "A piece of pale pink finger coral with five thick branches rising from a base so that it resembles an open hand, seen from the front."),
    ("손 모양 잎", "A single large green horse chestnut leaf with five leaflets spread like fingers, stem at the bottom edge, resembling an open hand."),
]
ITEMS = [(f"hand-{i + 1:02d}", "hand", l, p) for i, (l, p) in enumerate(HAND)] + [(f"other-{i + 1:02d}", "other", l, p) for i, (l, p) in enumerate(OTHER)]


def generate(prompt: str):
    key = next(l.split("=", 1)[1].strip().strip('"') for l in (ROOT.parent / ".env").read_text().splitlines() if l.startswith("OPENROUTER_API_KEY"))
    body = {"model": MODEL, "prompt": STYLE + prompt, "n": 1, "size": "1024x1024"}
    req = urllib.request.Request("https://openrouter.ai/api/v1/images/generations", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=300))
    return base64.b64decode(r["data"][0]["b64_json"]), r.get("usage", {}).get("cost")


def sheet():
    """모아보기 (_sheet.jpg): 손 15장 위, 손이 아닌 것 15장 아래, 한 줄 5장."""
    try:
        import cv2, numpy as np
    except ImportError:
        return
    tiles = []
    for name, *_ in ITEMS:
        im = cv2.imread(str(OUT / f"{name}.png"))
        if im is None:
            im = np.zeros((1024, 1024, 3), np.uint8)
        im = cv2.resize(im, (320, 320))
        cv2.putText(im, name, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        tiles.append(im)
    rows = [np.hstack(tiles[i:i + 5]) for i in range(0, len(tiles), 5)]
    cv2.imwrite(str(OUT / "_sheet.jpg"), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])


def main(only: list[str]):
    OUT.mkdir(parents=True, exist_ok=True)
    todo = [it for it in ITEMS if not only or it[0] in only]
    total = [0.0]

    def run(it):
        name, group, label, prompt = it
        t0 = time.time()
        for attempt in range(2):
            try:
                png, cost = generate(prompt)
                (OUT / f"{name}.png").write_bytes(png)
                total[0] += float(cost or 0)
                print(f"{name} {label}: 저장 ({time.time() - t0:.0f}s, ${cost})")
                return name, None
            except Exception as e:
                err = e
                time.sleep(3)
        print(f"{name} {label}: 실패 {err}")
        return name, str(err)

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(run, todo))
    manifest = {"model": MODEL, "question": "이 중 손이 아닌 것을 모두 고르시오",
                "items": [{"file": f"{n}.png", "group": g, "label": l, "prompt": p} for n, g, l, p in ITEMS]}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    sheet()
    print(f"합계 ${total[0]:.3f}")
    failed = [n for n, e in results if e]
    if failed:
        print("실패:", failed); sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
