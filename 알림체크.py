#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
인도네시아 증시 대시보드 알림체크 — GitHub Actions 하루 3번 실행
① AI 코멘트 (매 실행마다 카톡)
② 매수/손절 조건 충족 시 즉시 카톡
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

import yfinance as yf

KST = timezone(timedelta(hours=9))
STATE_FILE = os.path.join(os.path.dirname(__file__), "알림상태.json")
DASHBOARD_URL = "https://bssu3001-oss.github.io/indonesia-market-dashboard/"


# ── 상태 저장 (중복 발송 방지) ──
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def already_sent(state, key):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    run_slot = get_run_slot()
    return f"{key}_{run_slot}" in state.get(today, [])

def mark_sent(state, key):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    run_slot = get_run_slot()
    state.setdefault(today, [])
    slot_key = f"{key}_{run_slot}"
    if slot_key not in state[today]:
        state[today].append(slot_key)

def get_run_slot():
    hour = datetime.now(KST).hour
    if hour < 12:
        return "morning"
    elif hour < 17:
        return "afternoon"
    else:
        return "evening"


# ── 카카오 API ──
def kakao_get_access_token(rest_api_key, refresh_token, client_secret=None):
    params = {"grant_type": "refresh_token", "client_id": rest_api_key, "refresh_token": refresh_token}
    if client_secret:
        params["client_secret"] = client_secret
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        "https://kauth.kakao.com/oauth/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read())
    if "access_token" not in result:
        raise RuntimeError(f"토큰 갱신 실패: {result}")
    return result["access_token"]

def kakao_send(access_token, text):
    template = json.dumps({
        "object_type": "text",
        "text": text[:1000],
        "link": {"web_url": DASHBOARD_URL, "mobile_web_url": DASHBOARD_URL},
        "button_title": "대시보드 열기",
    }, ensure_ascii=False)
    data = urllib.parse.urlencode({"template_object": template}).encode()
    req = urllib.request.Request(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        data=data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read())
    if result.get("result_code") != 0:
        raise RuntimeError(f"메시지 전송 실패: {result}")
    print("✅ 카카오 전송 완료")


# ── 시장 데이터 수집 ──
def fetch_market_data():
    ticker = yf.Ticker("^JKSE")
    hist = ticker.history(period="1y", interval="1wk")
    prices = [float(r["Close"]) for _, r in hist.iterrows() if not r.isnull()["Close"]]

    try:
        fi = ticker.fast_info
        current = fi.last_price
        prev_close = fi.previous_close
        pct = (current - prev_close) / prev_close * 100
    except Exception:
        current = prices[-1]
        prev_close = prices[-2] if len(prices) >= 2 else current
        pct = (current - prev_close) / prev_close * 100

    ma5  = sum(prices[-5:])  / min(5,  len(prices))
    ma13 = sum(prices[-13:]) / min(13, len(prices))
    ma26 = sum(prices[-26:]) / min(26, len(prices))

    if ma5 > ma13 > ma26:
        ma_signal = "정배열(상승)"
    elif ma5 < ma13 < ma26:
        ma_signal = "역배열(하락)"
    else:
        ma_signal = "혼조"

    # RSI(14)
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains  = [d for d in deltas[-14:] if d > 0]
    losses = [-d for d in deltas[-14:] if d < 0]
    avg_g = sum(gains) / 14 if gains else 0
    avg_l = sum(losses) / 14 if losses else 1
    rsi = 100 - (100 / (1 + avg_g / avg_l)) if avg_l else 100

    # 4주 모멘텀
    mom4 = (prices[-1] - prices[-5]) / prices[-5] * 100 if len(prices) >= 5 else 0

    # 52주 위치
    hi52 = max(prices[-52:]) if len(prices) >= 52 else max(prices)
    lo52 = min(prices[-52:]) if len(prices) >= 52 else min(prices)
    from_hi = (current - hi52) / hi52 * 100

    # 연속 하락 주
    consec_down = 0
    for i in range(len(prices) - 1, 0, -1):
        if prices[i] < prices[i-1]:
            consec_down += 1
        else:
            break

    # 보조 지표
    us_vix = crude = usdidr = kol_pct = None
    eido = eido_pct = dxy = eem = eem_pct = kol = None
    for ticker_sym, var_name in [("^VIX", "us_vix"), ("BZ=F", "crude"), ("IDR=X", "usdidr"),
                                  ("KOL", "kol"), ("EIDO", "eido"), ("DX-Y.NYB", "dxy"), ("EEM", "eem")]:
        try:
            t = yf.Ticker(ticker_sym)
            val = t.fast_info.last_price
            prev_c = t.fast_info.previous_close
            chg = round((val - prev_c) / prev_c * 100, 2) if prev_c else 0
            if var_name == "us_vix":
                us_vix = round(val, 1)
            elif var_name == "crude":
                crude = round(val, 1)
            elif var_name == "usdidr":
                usdidr = round(val, 0)
            elif var_name == "kol":
                kol = round(val, 2); kol_pct = chg
            elif var_name == "eido":
                eido = round(val, 2); eido_pct = chg
            elif var_name == "dxy":
                dxy = round(val, 2)
            elif var_name == "eem":
                eem = round(val, 2); eem_pct = chg
        except Exception:
            pass

    return {
        "current": round(current, 2),
        "prev": round(prev_close, 2),
        "pct": round(pct, 2),
        "ma5": round(ma5, 2), "ma13": round(ma13, 2), "ma26": round(ma26, 2),
        "ma_signal": ma_signal,
        "rsi": round(rsi, 1),
        "mom4": round(mom4, 1),
        "from_hi": round(from_hi, 1),
        "us_vix": us_vix,
        "crude": crude,
        "usdidr": usdidr,
        "kol": kol,
        "kol_pct": kol_pct,
        "eido": eido,
        "eido_pct": eido_pct,
        "dxy": dxy,
        "eem": eem,
        "eem_pct": eem_pct,
        "consec_down": consec_down,
    }


# ── 인도네시아 증시 뉴스 헤드라인 수집 (구글 뉴스 RSS) ──
def fetch_news_headlines(max_items=3):
    import xml.etree.ElementTree as ET
    import re
    EXCLUDE = ['한국 증시', '코스피', '코스닥', '삼성전자', '인도 증시', '인도가']
    urls = [
        'https://news.google.com/rss/search?q=%EC%9D%B8%EB%8F%84%EB%84%A4%EC%8B%9C%EC%95%84+%EC%A6%9D%EC%8B%9C&hl=ko&gl=KR&ceid=KR:ko',
        'https://news.google.com/rss/search?q=IHSG+%EC%9D%B8%EB%8F%84%EB%84%A4%EC%8B%9C%EC%95%84&hl=ko&gl=KR&ceid=KR:ko',
    ]
    titles = []
    for url in urls:
        if len(titles) >= max_items:
            break
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                xml = r.read()
            root = ET.fromstring(xml)
            for item in root.iter('item'):
                t = item.findtext('title') or ''
                t = re.sub(r'\s*-\s*\S+$', '', t).strip()
                if not t or any(kw in t for kw in EXCLUDE):
                    continue
                if t not in titles:
                    titles.append(t)
                if len(titles) >= max_items:
                    break
        except Exception as e:
            print(f"  뉴스 수집 실패: {e}")
    return titles


# ── 종합신호 점수 계산 ──
def calc_scorecard(data, news_titles=None):
    score = 0
    max_score = 0

    rsi = data["rsi"]
    ma  = data["ma_signal"]
    mom = data["mom4"]
    fhi = data["from_hi"]

    rsi_s = 1 if rsi <= 40 else (-1 if rsi >= 70 else 0)
    ma_s  = 1 if "정배열" in ma else (-1 if "역배열" in ma else 0)
    mom_s = 1 if mom >= 2 else (-1 if mom <= -2 else 0)
    vol_s = 0
    pos_s = 1 if fhi <= -20 else (-1 if fhi >= -3 else 0)

    for s in [rsi_s, ma_s, mom_s, vol_s, pos_s]:
        score += s * 1.5
        max_score += 1.5

    vix_u = data["us_vix"] or 0
    crude = data["crude"] or 80
    usdidr = data["usdidr"] or 15500
    kol_pct = data["kol_pct"] or 0

    vix_u_s  = 1 if vix_u < 20 else (-1 if vix_u > 28 else 0)
    crude_s  = 1 if crude < 75 else (-1 if crude > 85 else 0)
    idr_s    = 1 if usdidr < 15500 else (-1 if usdidr > 16500 else 0)
    kol_s    = 1 if kol_pct >= 0.5 else (-1 if kol_pct <= -0.5 else 0)

    for s in [vix_u_s, crude_s, idr_s, kol_s]:
        score += s * 1.0
        max_score += 1.0

    pct = max(0, min(100, round((score + max_score) / (2 * max_score) * 100)))

    if score >= max_score * 0.5:    label, emoji = "강매수", "🔥"
    elif score >= max_score * 0.15: label, emoji = "매수 검토", "🟢"
    elif score >= -max_score * 0.15: label, emoji = "관망", "📌"
    elif score >= -max_score * 0.5:  label, emoji = "조심", "⚠️"
    else:                             label, emoji = "진입 자제", "🔴"

    ma_txt = "정배열(상승)" if "정배열" in ma else ("역배열(하락)" if "역배열" in ma else "혼조")
    mom_txt = f"4주 모멘텀 강세(+{mom:.1f}%)" if mom >= 2 else (f"4주 모멘텀 약세({mom:.1f}%)" if mom <= -2 else f"4주 모멘텀 보합({mom:.1f}%)")
    vix_u_txt = f"미국 VIX {vix_u}({'안정' if vix_u < 20 else '주의' if vix_u < 28 else '공포'})" if vix_u else ""
    idr_txt = f"USD/IDR Rp{int(usdidr):,}({'루피아 약세' if usdidr > 16500 else '루피아 안정'})" if usdidr else "USD/IDR N/A"
    crude_txt = f"유가 ${crude}({'부담' if crude > 85 else '안정'})" if crude else ""
    kol_txt = f"석탄 KOL {kol_pct:+.2f}%({'호재' if kol_pct >= 0.5 else '악재' if kol_pct <= -0.5 else '보합'})" if kol_pct is not None else ""

    trend_comment = (
        "분할 매수 단계적 검토 가능." if "정배열" in ma and score >= 0
        else "추세 회복 확인 후 진입 권장." if "역배열" in ma
        else "관망하며 추가 신호 대기."
    )

    parts = [f"이평선 {ma_txt}", mom_txt, idr_txt]
    if vix_u_txt: parts.append(vix_u_txt)
    if crude_txt: parts.append(crude_txt)
    if kol_txt: parts.append(kol_txt)
    desc = ", ".join(parts) + f". {label} 판단 — {trend_comment}"

    if news_titles:
        news_str = " / ".join(news_titles)
        desc += f"\n📰 주요 뉴스: {news_str}"

    return pct, label, emoji, desc


# ── 알림 조건 체크 ──
def check_conditions(data):
    alerts = []
    ihsg = data["current"]
    rsi = data["rsi"]
    us_vix = data["us_vix"]
    ma_signal = data["ma_signal"]
    usdidr = data["usdidr"] or 0

    # ── 매수 신호 ──
    if rsi <= 35:
        alerts.append({"type": "매수핵심",
            "msg": f"🟢 [인도네시아증시] 매수 신호!\nRSI {rsi} — 과매도 구간 진입\nIHSG {ihsg:,.2f} | 1차 매수 검토하세요"})

    if ma_signal == "정배열(상승)" and data["mom4"] > 0:
        alerts.append({"type": "매수핵심",
            "msg": f"🟢 [인도네시아증시] 매수 신호!\n이평선 정배열 + 모멘텀 상승 ({data['mom4']:+}%)\nIHSG {ihsg:,.2f} | 추세 추종 매수 검토"})

    if usdidr and usdidr < 15200 and rsi <= 50:
        alerts.append({"type": "매수참고",
            "msg": f"📊 [인도네시아증시] 매수 참고\nUSD/IDR Rp{int(usdidr):,} (루피아 강세) + RSI {rsi}\n환율 안정 + 과매도 눌림목 구간"})

    if data["from_hi"] <= -15 and rsi <= 45:
        alerts.append({"type": "매수참고",
            "msg": f"📊 [인도네시아증시] 매수 참고\n52주 고점 대비 {data['from_hi']}% + RSI {rsi}\n저점 매수 구간 진입"})

    # ── 주의/손절 신호 ──
    if usdidr and usdidr >= 16800:
        alerts.append({"type": "주의",
            "msg": f"⚠️ [인도네시아증시] 주의\nUSD/IDR Rp{int(usdidr):,} — 루피아 급락\n외국인 자금 유출 우려, 신규 매수 자제"})

    if us_vix and us_vix >= 28:
        alerts.append({"type": "주의",
            "msg": f"⚠️ [인도네시아증시] 글로벌 경보\n미국 VIX {us_vix} — 공포 구간\n포지션 축소 검토"})

    if data["consec_down"] >= 3:
        alerts.append({"type": "주의",
            "msg": f"⚠️ [인도네시아증시] 주의\nIHSG {data['consec_down']}주 연속 하락\n추가 하락 가능성, 관망 권장"})

    if ihsg <= 6000:
        alerts.append({"type": "손절",
            "msg": f"🔴 [인도네시아증시] 손절 경고!\nIHSG {ihsg:,.2f} — 주요 지지선 붕괴\n손절 기준 재점검하세요"})

    # ── 호재 이벤트 ──
    if ma_signal == "정배열(상승)" and data["pct"] >= 1.5:
        alerts.append({"type": "호재",
            "msg": f"🚀 [인도네시아증시] 강세 신호!\nIHSG {ihsg:,.2f} (+{data['pct']}%) 상승\n이평선 정배열 유지 중"})

    return alerts


# ── 메인 ──
def main():
    rest_api_key    = os.environ.get("KAKAO_REST_API_KEY", "").strip()
    refresh_token   = os.environ.get("KAKAO_REFRESH_TOKEN", "").strip()
    client_secret   = os.environ.get("KAKAO_CLIENT_SECRET", "").strip() or None
    anthropic_key   = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    kakao_available = bool(rest_api_key and refresh_token)
    if not kakao_available:
        print("⚠️  KAKAO 환경변수 없음 — 카카오 알림 건너뜀, 데이터 수집은 계속")

    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    slot_kr = {"morning": "오전", "afternoon": "오후", "evening": "저녁"}.get(get_run_slot(), "")
    today = datetime.now(KST).strftime("%Y-%m-%d")

    print(f"[{now_kst}] 인도네시아증시 알림체크 시작")
    state = load_state()

    access_token = None
    if kakao_available:
        print("카카오 토큰 갱신 중...")
        access_token = kakao_get_access_token(rest_api_key, refresh_token, client_secret)

    print("IHSG 데이터 수집 중...")
    data = fetch_market_data()
    print(f"IHSG: {data['current']:,.2f} ({data['pct']:+}%) | RSI: {data['rsi']} | 이평: {data['ma_signal']}")

    print("뉴스 수집 중...")
    news_titles = fetch_news_headlines(max_items=3)
    print(f"뉴스 {len(news_titles)}건: {news_titles}")

    pct_score, sc_label, sc_emoji, sc_desc = calc_scorecard(data, news_titles)
    pct_str = f"+{data['pct']:.2f}" if data['pct'] >= 0 else f"{data['pct']:.2f}"

    # ① 시황 알림 (실행마다 1회)
    if not already_sent(state, "ai_comment"):
        usdidr_str = f"Rp{int(data['usdidr']):,}" if data['usdidr'] else "N/A"
        msg = (f"🇮🇩 인도네시아 증시 {slot_kr} 시황 [{now_kst}]\n\n"
               f"IHSG: {data['current']:,.2f} ({pct_str}%)\n"
               f"이평선: {data['ma_signal']} | RSI: {data['rsi']}\n"
               f"미국 VIX: {data['us_vix'] or 'N/A'} | USD/IDR: {usdidr_str}\n\n"
               f"━━━━━━━━━━━━\n"
               f"종합신호: {sc_emoji} {sc_label} ({pct_score}점)\n"
               f"{sc_desc}")
        if access_token:
            kakao_send(access_token, msg)
        mark_sent(state, "ai_comment")
        save_state(state)

    # ② 조건 알림
    alerts = check_conditions(data)
    priority = {"손절": 0, "주의": 1, "매수핵심": 2, "호재": 3, "매수참고": 4}
    alerts.sort(key=lambda a: priority.get(a["type"], 99))

    for alert in alerts:
        key = alert["type"]
        if already_sent(state, key):
            print(f"  ⏭ 오늘 이 슬롯에 이미 보낸 알림 스킵: {key}")
            continue
        if access_token:
            kakao_send(access_token, alert["msg"])
        mark_sent(state, key)
        save_state(state)
        print(f"  → {key}: {alert['msg'][:50]}...")

    if not alerts:
        print("✅ 조건 알림 없음")

    # 오래된 상태 정리 (3일 이전 삭제)
    cutoff = (datetime.now(KST) - timedelta(days=3)).strftime("%Y-%m-%d")
    for d in list(state.keys()):
        if d < cutoff:
            del state[d]
    save_state(state)

    # 시장데이터.json 저장 (대시보드 자동갱신용)
    market_json = {
        "updated": now_kst,
        "ihsg": data["current"],
        "pct": data["pct"],
        "ma_signal": data["ma_signal"],
        "rsi": data["rsi"],
        "mom4": data["mom4"],
        "from_hi": data["from_hi"],
        "us_vix": data["us_vix"],
        "crude": data["crude"],
        "usdidr": data["usdidr"],
        "kol": data.get("kol"),
        "kol_pct": data["kol_pct"],
        "eido": data.get("eido"),
        "eido_pct": data.get("eido_pct"),
        "dxy": data.get("dxy"),
        "eem": data.get("eem"),
        "eem_pct": data.get("eem_pct"),
        "score_pct": pct_score,
        "score_label": sc_label,
        "score_emoji": sc_emoji,
        "score_desc": sc_desc,
    }
    with open("시장데이터.json", "w", encoding="utf-8") as f:
        json.dump(market_json, f, ensure_ascii=False, indent=2)
    print("시장데이터.json 저장 완료")
    print("완료")


if __name__ == "__main__":
    main()
