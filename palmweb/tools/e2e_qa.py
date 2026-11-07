# e2e QA (헤드리스 Chromium): 체험 화면(사진 입력) → 단계 넘김 → "여기서 멈춤" → 닉네임 제출 → 대시보드 실시간 반영 → 초기화.
#   uv run --with playwright python tools/e2e_qa.py        (처음 한 번: uv run --with playwright playwright install chromium)
#   E2E_URL=http://localhost:8011 로 대상 서버를 바꾼다. 스크린샷은 logs/ 에 남는다. 마지막에 초기화를 누르므로 전시 중 서버에 돌리지 말 것.
import asyncio, sys
from playwright.async_api import async_playwright
BASE = __import__("os").environ.get("E2E_URL", "http://localhost:8000")
OUT = __import__("os").environ.get("E2E_OUT", "logs"); __import__("os").makedirs(OUT, exist_ok=True)
R = []
def check(name, ok, info=""):
    R.append((name, ok, info)); print(("PASS" if ok else "FAIL"), name, info)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        dash = await b.new_page(viewport={"width": 1920, "height": 1080})
        await dash.goto(BASE + "/dashboard"); await dash.wait_for_timeout(1500)
        n0 = int(await dash.text_content("#count"))
        check("대시보드 로드·초기 기록 수", n0 >= 0, f"{n0}명")

        ex = await b.new_page(viewport={"width": 1920, "height": 1080})
        logs = []
        ex.on("console", lambda m: logs.append(m.text))
        await ex.goto(BASE + "/?src=/samples/hand70.jpg&room=exhibit&controls=0")
        for _ in range(60):   # 캡처·해석 대기
            if await ex.evaluate("window.__palm && window.__palm.getState()") == "result": break
            await ex.wait_for_timeout(500)
        check("체험: 사진 입력으로 결과 화면 도달", await ex.evaluate("window.__palm.getState()") == "result")
        for _ in range(40):
            if await ex.evaluate("window.__palm.interps.size") >= 6: break
            await ex.wait_for_timeout(500)
        check("체험: 자막(해석 텍스트) 도착", await ex.evaluate("window.__palm.interps.size") >= 6, f"{await ex.evaluate('window.__palm.interps.size')}/10")
        await ex.wait_for_timeout(2500)   # 자막은 타이핑되듯 나타난다
        sub = await ex.evaluate("document.querySelector('#subtitle .subtitle-inner').textContent")
        check("체험: 화면에 자막 텍스트 표시", bool(sub and sub.strip()), sub[:40])
        vis = await ex.evaluate("getComputedStyle(document.getElementById('stop-btn')).display")
        check("체험: 여기서 멈춤 버튼 보임", vis != "none", vis)
        for _ in range(6): await ex.keyboard.press("ArrowRight"); await ex.wait_for_timeout(120)
        step = await ex.evaluate("window.__palm.getStep()")
        check("체험: 방향키로 7단계", step == 7, str(step))
        await ex.screenshot(path=f"{OUT}/e2e_exhibit_step7.png")
        await ex.click("#stop-btn"); await ex.wait_for_timeout(300)
        check("모달: 열림, 단계 표시", not await ex.evaluate("document.getElementById('submit-modal').hidden") and await ex.text_content("#submit-step") == "7")
        mtext = await ex.text_content("#submit-text")
        check("모달: 현재 문장 표시", bool(mtext.strip()), mtext[:30])
        await ex.keyboard.type("r")   # 닉네임 입력 중 R 단축키가 restart 를 부르면 안 된다
        check("모달: 입력 중 R 키가 재시작하지 않음", await ex.evaluate("window.__palm.getState()") == "result")
        await ex.fill("#nickname", "QA봇")
        await ex.screenshot(path=f"{OUT}/e2e_modal.png")
        await ex.keyboard.press("Enter"); await ex.wait_for_timeout(1500)
        check("제출: 감사 화면", not await ex.evaluate("document.getElementById('thanks').hidden"))
        await ex.screenshot(path=f"{OUT}/e2e_thanks.png")
        await dash.wait_for_timeout(500)
        n1 = int(await dash.text_content("#count"))
        first = await dash.text_content("#cards .card .nick")
        check("대시보드: 실시간으로 1건 추가", n1 == n0 + 1 and first == "QA봇", f"{n0}→{n1}, 첫 카드={first}")
        check("대시보드: 상세가 새 기록으로", await dash.text_content("#d-nick") == "QA봇" and await dash.text_content("#d-step") == "7")
        img = await dash.get_attribute("#d-img", "src")
        r = await dash.request.get(BASE + img)
        check("대시보드: 손 스냅샷 저장·표시", r.ok and (await r.body())[:2] == b"\xff\xd8", img)
        nat = await dash.evaluate("(() => { const i = document.getElementById('d-img'); return [i.naturalWidth, i.naturalHeight]; })()")
        check("대시보드: 스냅샷 이미지 로드", nat[0] > 0, str(nat))
        tov = await dash.evaluate("(() => { const t = document.getElementById('d-text'); return [t.scrollHeight - t.clientHeight, t.textContent.length]; })()")
        check("대시보드: 글이 잘리지 않고 전부 보임", tov[0] <= 1, f"넘침 {tov[0]}px, {tov[1]}자")
        thumbs = await dash.evaluate("Array.from(document.querySelectorAll('#cards .thumb')).filter(i => i.tagName==='IMG' && i.naturalWidth>0).length")
        check("대시보드: 카드 썸네일 로드", thumbs >= 1, f"{thumbs}장")
        sc = await dash.evaluate("[document.documentElement.scrollHeight, innerHeight, document.documentElement.scrollWidth, innerWidth]")
        check("대시보드: 페이지 스크롤 없음 1920x1080", sc[0] <= sc[1] and sc[2] <= sc[3], str(sc))
        await dash.screenshot(path=f"{OUT}/e2e_dash_1920.png")
        for w, h in [(1366, 768), (1280, 720), (2560, 1440), (1080, 1920)]:
            await dash.set_viewport_size({"width": w, "height": h}); await dash.wait_for_timeout(400)
            sc = await dash.evaluate("[document.documentElement.scrollHeight, innerHeight, document.documentElement.scrollWidth, innerWidth]")
            # 패널 안에서 넘치는 요소(잘림)가 있는지: 각 pane 의 scrollHeight > clientHeight
            ov = await dash.evaluate("Array.from(document.querySelectorAll('.top,.list,.detail,#d-text')).map(e => [e.className || e.id, e.scrollHeight - e.clientHeight]).filter(x => x[1] > 2)")
            check(f"대시보드: 스크롤 없음 {w}x{h}", sc[0] <= sc[1] and sc[2] <= sc[3], str(sc))
            check(f"대시보드: 패널 내부 잘림 없음 {w}x{h}", not ov, str(ov))
            await dash.screenshot(path=f"{OUT}/e2e_dash_{w}x{h}.png")
        await dash.set_viewport_size({"width": 1920, "height": 1080})

        # 다음 관객: 감사 화면이 끝나면 촬영 화면으로
        await ex.wait_for_timeout(4000)
        st = await ex.evaluate("window.__palm.getState()")
        check("체험: 제출 뒤 다음 관객을 위해 촬영/결과로 복귀", st in ("tracking", "capturing", "result"), st)
        await ex.reload(); await ex.wait_for_timeout(1500)
        check("체험: 새로 고침 후 촬영부터 시작", await ex.evaluate("window.__palm.getState()") in ("tracking", "capturing", "result"))

        # 초기화
        await dash.click("#reset"); await dash.wait_for_timeout(200)
        check("초기화: 확인 단계 표시", not await dash.evaluate("document.getElementById('reset-confirm').hidden") and await dash.text_content("#reset-n") == str(n1))
        await dash.keyboard.press("Escape")
        check("초기화: Esc 로 취소", await dash.evaluate("document.getElementById('reset-confirm').hidden"))
        await dash.click("#reset"); await dash.click("#reset-yes"); await dash.wait_for_timeout(800)
        check("초기화: 대시보드 비워짐", await dash.text_content("#count") == "0" and not await dash.evaluate("document.getElementById('empty').hidden"))
        await dash.screenshot(path=f"{OUT}/e2e_dash_empty.png")
        errs = [l for l in logs if l.lower().startswith(("error", "uncaught")) or "TypeError" in l or "ReferenceError" in l]
        check("체험: 콘솔 에러 없음", not errs, "; ".join(errs)[:200])
        await b.close()
    fails = [r for r in R if not r[1]]
    print(f"\n{len(R)-len(fails)}/{len(R)} 통과")
    sys.exit(1 if fails else 0)
asyncio.run(main())
