import requests
import json
import hashlib
import re
import time
import os
from urllib.parse import urlparse, quote, urlsplit, urlunsplit, unquote
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# ─────────────────────────────────────────────────────────────────────────────
# TIMEZONE & CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

VN_TZ = timezone(timedelta(hours=7))

FULL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

def now_vn() -> datetime:
    return datetime.now(tz=VN_TZ)

def parse_kickoff(time_str: str):
    if not time_str: return None
    try:
        s = time_str.strip()
        tz_part = re.search(r'([+-])(\d{2})(?::(\d{2}))?$', s)
        if tz_part:
            sign, hh, mm = tz_part.group(1), tz_part.group(2), tz_part.group(3)
            fixed_tz = f"{sign}{hh}:{mm or '00'}"
            s = s[:tz_part.start()] + fixed_tz
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=VN_TZ)
        return dt
    except Exception:
        return None

def format_match_time(time_str: str) -> str:
    dt = parse_kickoff(time_str)
    return dt.strftime("%H:%M %d/%m") if dt else time_str

def parse_time_sort(time_str: str) -> int:
    dt = parse_kickoff(time_str)
    return int(dt.timestamp()) if dt else 9999999999

# Sửa mojibake (UTF-8 bị đọc nhầm Latin-1/cp1252) + percent-encode URL an toàn
def sanitize_url(url: str) -> str:
    if not url:
        return ""
    u = unquote(url.strip())
    for enc in ("cp1252", "latin-1"):
        try:
            u = u.encode(enc).decode("utf-8")
            break
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    try:
        p = urlsplit(u)
        u = urlunsplit((
            p.scheme, p.netloc,
            quote(p.path, safe="/-._~()"),
            quote(p.query, safe="=&/-._~()"),
            p.fragment,
        ))
    except Exception:
        pass
    return u

# Tách giờ/ngày theo schema Giovang: time="HH:MM:SS", date="dd/MM"
def split_match_datetime(time_raw: str, time_display: str):
    dt = parse_kickoff(time_raw)
    if dt:
        return dt.strftime("%H:%M:%S"), dt.strftime("%d/%m")
    m = re.match(r'(\d{1,2}:\d{2})\s+(\d{1,2}/\d{1,2})', (time_display or "").strip())
    if m:
        return f"{m.group(1)}:00", m.group(2)
    return (time_display or ""), ""

def make_id(text, prefix):
    return f"{prefix}-{hashlib.md5(text.encode()).hexdigest()[:10]}"

def fetch_image(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:
        return None

def validate_stream(url, match):
    is_live = match.get("is_live", False)
    try:
        kickoff = parse_kickoff(match.get("time_raw", ""))
        now = now_vn()
        is_future_safe = (not is_live) and kickoff and kickoff > now + timedelta(minutes=15)
        res = requests.get(url, headers=HEADERS, timeout=6, stream=True)
        if res.status_code in [404, 500, 502, 503, 504] and is_future_safe:
            res.close()
            return True
        if res.status_code != 200:
            res.close()
            print(f"    ❌ HTTP {res.status_code} từ stream")
            return False
        first_chunk = next(res.iter_content(1024), b"")
        res.close()
        if b"#EXTM3U" in first_chunk or b"#EXTINF" in first_chunk or b"#EXT-X" in first_chunk:
            return True
        print(f"    ❌ Không phải HLS, first bytes: {first_chunk[:100]}")
        return is_future_safe
    except Exception as e:
        print(f"    ❌ Exception khi validate stream: {e}")
        if is_live:
            return False
        kickoff = parse_kickoff(match.get("time_raw", ""))
        now = now_vn()
        return bool(kickoff and kickoff > now + timedelta(minutes=15))

# ─────────────────────────────────────────────────────────────────────────────
# AUTO-RESOLVE SITE DOMAIN
# ─────────────────────────────────────────────────────────────────────────────

def resolve_site_domain(entry_url, default_domain):
    try:
        print(f"  Dang kiem tra redirect tu: {entry_url}")
        res = requests.get(
            entry_url,
            headers={"User-Agent": FULL_UA},
            timeout=10,
            allow_redirects=True
        )
        final_url = res.url
        parsed = urlparse(final_url)
        netloc = parsed.netloc
        if netloc.startswith("www."):
            netloc = netloc[4:]
        site_url = f"{parsed.scheme}://{netloc}"
        api_domain = netloc
        if site_url and site_url != entry_url.rstrip('/'):
            print(f"  ✅ Phat hien domain moi: {site_url}")
            print(f"  ✅ API domain: api.{api_domain}")
            return site_url, site_url + "/", api_domain
        else:
            print(f"  ℹ️  Khong co redirect, giu nguyen: {default_domain}")
            return f"https://{default_domain}", f"https://{default_domain}/", default_domain
    except Exception as e:
        print(f"  ⚠️  Loi khi resolve domain: {e} -> dung gia tri mac dinh")
        return f"https://{default_domain}", f"https://{default_domain}/", default_domain

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": FULL_UA,
    "Referer": "https://choangtv21.com/",
}

ENTRY_SITE_URL   = "https://choangtv.com/"
DEFAULT_DOMAIN   = "choangtv21.com"
CDN_BASE         = "https://cdn.sports-cas889abxfileposo.site/live"
SITE_URL         = f"https://{DEFAULT_DOMAIN}"
API_URL          = f"https://api.{DEFAULT_DOMAIN}/matchSchedule/getList"

THUMBS_DIR = "thumbs"
REPO_RAW   = os.environ.get("REPO_RAW", "")
THUMB_VER  = "v1"

# 🛡️ Regex patterns phân loại bộ môn (Word Boundary)
BILLIARD_PATTERN = re.compile(r'\b(pool|billiard|bida|9[- ]?ball|10[- ]?ball|8[- ]?ball|carom|snooker)\b', re.IGNORECASE)
MARTIAL_PATTERN  = re.compile(r'\b(inner\s*circle|võ\s+thuật|mma|muay|ufc|boxing|kickboxing)\b', re.IGNORECASE)

# ─────────────────────────────────────────────────────────────────────────────
# THUMBNAIL
# ─────────────────────────────────────────────────────────────────────────────

def make_thumbnail(match, channel_id):
    os.makedirs(THUMBS_DIR, exist_ok=True)
    cache_key = match.get("logo_a", "") + match.get("logo_b", "") + THUMB_VER
    logo_hash = hashlib.md5(cache_key.encode()).hexdigest()[:8]
    date_str  = now_vn().strftime("%Y%m%d")
    out_path  = f"{THUMBS_DIR}/{channel_id}_{logo_hash}_{date_str}.png"

    if os.path.exists(out_path):
        return out_path

    W, H = 1600, 1200
    HEADER_H, FOOTER_H = 180, 160
    ACCENT = (220, 30, 40)

    bg   = Image.new("RGB", (W, H), (245, 245, 248))
    draw = ImageDraw.Draw(bg)

    for y in range(HEADER_H, H - FOOTER_H):
        ratio = (y - HEADER_H) / (H - FOOTER_H - HEADER_H)
        gray  = int(248 - ratio * 18)
        draw.line([(0, y), (W, y)], fill=(gray, gray, gray + 4))

    draw.rectangle([(0, 0), (W, HEADER_H)], fill=(13, 20, 40))
    draw.rectangle([(0, H - FOOTER_H), (W, H)], fill=(13, 20, 40))
    draw.rectangle([(0, HEADER_H), (W, HEADER_H + 5)], fill=ACCENT)
    draw.rectangle([(0, H - FOOTER_H - 5), (W, H - FOOTER_H)], fill=ACCENT)

    FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        font_vs   = ImageFont.truetype(FONT_BOLD, 160)
        font_time = ImageFont.truetype(FONT_BOLD, 100)
        font_team = ImageFont.truetype(FONT_BOLD, 58)
    except Exception:
        font_vs = font_time = font_team = ImageFont.load_default()

    content_top = HEADER_H + 5
    content_bot = H - FOOTER_H - 5
    content_h   = content_bot - content_top

    logo_size, name_h, time_h = 360, 120, 110
    gap_logo_name, gap_name_time = 40, 60
    total_block_h = logo_size + gap_logo_name + name_h + gap_name_time + time_h
    block_top     = content_top + (content_h - total_block_h) // 2

    logo_y       = block_top
    name_center  = logo_y + logo_size + gap_logo_name + name_h // 2
    time_y       = name_center + name_h // 2 + gap_name_time + time_h // 2

    for key, cx in [("logo_a", W // 4), ("logo_b", W * 3 // 4)]:
        if match.get(key):
            img = fetch_image(match[key])
            if img:
                img = img.resize((logo_size, logo_size), Image.LANCZOS)
                bg.paste(img, (cx - logo_size // 2, logo_y), img)

    draw.text((W // 2, logo_y + logo_size // 2), "VS",
              fill=ACCENT, font=font_vs, anchor="mm")

    def draw_team_name(text, cx):
        fs = 58
        f = font_team
        while fs >= 28:
            try:
                f = ImageFont.truetype(FONT_BOLD, fs)
            except Exception:
                f = ImageFont.load_default()
            if (draw.textbbox((0, 0), text, font=f)[2] - draw.textbbox((0, 0), text, font=f)[0]) <= W // 2 - 60:
                break
            fs -= 3
        draw.text((cx, name_center), text, fill=(20, 20, 20), font=f, anchor="mm")

    if match.get("team_a"):
        draw_team_name(match["team_a"], W // 4)
    if match.get("team_b"):
        draw_team_name(match["team_b"], W * 3 // 4)

    if match.get("time_display"):
        draw.text((W // 2 + 4, time_y + 4), match["time_display"],
                  fill=ACCENT, font=font_time, anchor="mm")
        draw.text((W // 2, time_y), match["time_display"],
                  fill=(15, 15, 15), font=font_time, anchor="mm")

    if match.get("league"):
        txt = match["league"].upper()
        fs = 62
        f = None
        while fs >= 28:
            try:
                f = ImageFont.truetype(FONT_BOLD, fs)
            except Exception:
                f = ImageFont.load_default()
            if (draw.textbbox((0, 0), txt, font=f)[2] - draw.textbbox((0, 0), txt, font=f)[0]) <= W - 60:
                break
            fs -= 3
        draw.text((W // 2, HEADER_H // 2), txt, fill=(255, 255, 255), font=f, anchor="mm")

    draw.rectangle([(0, 0), (W - 1, H - 1)], outline=(180, 180, 180), width=3)
    bg.save(out_path, "PNG", optimize=True)
    return out_path

def cleanup_old_thumbs(days: int = 3):
    if not os.path.exists(THUMBS_DIR): return
    cutoff  = now_vn() - timedelta(days=days)
    removed = 0
    for fname in os.listdir(THUMBS_DIR):
        if not fname.endswith(".png"): continue
        m = re.search(r'_(\d{8})\.png$', fname)
        fpath = os.path.join(THUMBS_DIR, fname)
        if not m:
            try: os.remove(fpath); removed += 1
            except Exception: pass
            continue
        try:
            if datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=VN_TZ) < cutoff:
                os.remove(fpath); removed += 1
        except ValueError: pass
    if removed:
        print(f"Da xoa {removed} thumbnail cu (>{days} ngay)")

# ─────────────────────────────────────────────────────────────────────────────
# SCRAPE MATCHES
# ─────────────────────────────────────────────────────────────────────────────

def get_matches():
    today = now_vn()
    dates_to_fetch = [
        today - timedelta(days=1),
        today,
        today + timedelta(days=1)
    ]

    all_matches = []
    seen_ids    = set()

    for date in dates_to_fetch:
        date_str = date.strftime("%Y-%m-%d")
        try:
            res = requests.get(API_URL, params={"date": date_str}, headers=HEADERS, timeout=15)
            data = res.json()
        except Exception as e:
            print(f"  ❌ Loi API date={date_str}: {e}")
            continue

        if data.get("code") != 200: continue

        for item in data.get("data", []):
            match_id = str(item.get("id", ""))
            if not match_id or match_id in seen_ids: continue
            seen_ids.add(match_id)
            if item.get("end", False): continue

            time_raw     = item.get("time") or ""
            time_display = format_match_time(time_raw)
            time_only, date_only = split_match_datetime(time_raw, time_display)
            team_a       = (item.get("name1") or "").strip()
            team_b       = (item.get("name2") or "").strip()
            logo_a       = sanitize_url(item.get("logo1") or "")
            logo_b       = sanitize_url(item.get("logo2") or "")
            league       = (item.get("league") or "").strip()
            caster_raw   = (item.get("caster") or "").strip()
            score1       = item.get("score1") or 0
            score2       = item.get("score2") or 0
            is_live      = bool(item.get("live", False))
            category     = (item.get("category") or "Billiards").lower()

            caster_clean = re.sub(r'^BLV\s*', '', caster_raw).strip()
            if not caster_clean: caster_clean = ""

            name = f"{team_a} vs {team_b}"
            if not name.replace("vs", "").strip(): name = f"Tran {match_id}"

            all_matches.append({
                "match_id": match_id, "name": name, "time": time_display,
                "time_display": time_display, "time_raw": time_raw,
                "time_sort": parse_time_sort(time_raw),
                "time_only": time_only, "date_only": date_only,
                "team_a": team_a, "team_b": team_b,
                "logo_a": logo_a, "logo_b": logo_b,
                "league": league, "caster": caster_clean,
                "score1": score1, "score2": score2,
                "is_live": is_live, "hot": bool(item.get("hot", False)),
                "subtitle": item.get("subtitle") or "",
                "category": category,
                "stream_url": f"{CDN_BASE}/live{match_id}/index.m3u8",
            })

    print(f"  Tong so tran tu API: {len(all_matches)}")

    now = now_vn()
    min_past   = now - timedelta(minutes=30)
    max_future = now + timedelta(hours=6)

    time_filtered = []
    for m in all_matches:
        is_live = m.get("is_live", False)
        kickoff = parse_kickoff(m["time_raw"])
        if is_live:
            time_filtered.append(m)
            continue
        if kickoff and (kickoff < min_past or kickoff > max_future):
            continue
        time_filtered.append(m)

    print(f"  Filter thoi gian: {len(all_matches)} -> {len(time_filtered)} tran")

    def clean_team_name(name):
        return re.sub(r'\s*[\(\[][\+\-]?\d+([.,]\d+)?(win)?[\)\]]', '', name).strip()

    def get_group_key(match):
        text_to_check = f"{match.get('name', '')} {match.get('league', '')} {match.get('category', '')}"

        is_billiard = bool(BILLIARD_PATTERN.search(text_to_check))
        is_martial  = bool(MARTIAL_PATTERN.search(text_to_check))

        league = match.get("league", "")
        league_clean = re.sub(r'Trận đấu \d+\s*\|\s*', '', league).strip()
        kickoff = parse_kickoff(match.get("time_raw", ""))
        date_key = kickoff.strftime("%Y-%m-%d") if kickoff else ""

        if is_martial and not is_billiard:
            return ("MARTIAL", league_clean.lower(), date_key)
        else:
            team_a = clean_team_name(match.get("team_a", ""))
            team_b = clean_team_name(match.get("team_b", ""))
            teams = tuple(sorted([team_a.lower(), team_b.lower()]))
            time_key = kickoff.strftime("%Y-%m-%d %H:%M") if kickoff else match.get("time_raw", "")
            return ("BILLIARD", league_clean.lower(), teams, time_key)

    def select_representative(matches_list):
        matches_list.sort(key=lambda x: (0 if x["is_live"] else 1, x["time_sort"]))
        for m in matches_list:
            if m["is_live"]: return m
        for m in matches_list:
            if m.get("caster"): return m
        now_local = now_vn()
        for m in matches_list:
            kickoff = parse_kickoff(m["time_raw"])
            if kickoff and now_local < kickoff < now_local + timedelta(hours=1): return m
        return matches_list[0] if matches_list else None

    live_matches = []
    upcoming_matches = []

    for m in time_filtered:
        if m.get("is_live", False):
            live_matches.append(m)
        else:
            upcoming_matches.append(m)

    print(f"  Tach: {len(live_matches)} LIVE + {len(upcoming_matches)} SAP")

    grouped_upcoming = {}
    for m in upcoming_matches:
        key = get_group_key(m)
        if key not in grouped_upcoming: grouped_upcoming[key] = []
        grouped_upcoming[key].append(m)

    final_upcoming = []
    for key, matches_in_event in grouped_upcoming.items():
        rep = select_representative(matches_in_event)
        if rep:
            if key[0] == "MARTIAL":
                league_clean = re.sub(r'Trận đấu \d+\s*\|\s*', '', rep.get("league", "")).strip()
                if league_clean:
                    rep["name"] = league_clean
                    rep["team_a"] = ""
                    rep["team_b"] = ""
            else:
                rep["team_a"] = clean_team_name(rep.get("team_a", ""))
                rep["team_b"] = clean_team_name(rep.get("team_b", ""))
                rep["name"] = f"{rep['team_a']} vs {rep['team_b']}"
            final_upcoming.append(rep)
            if len(matches_in_event) > 1:
                print(f"  Gom nhom SAP: {len(matches_in_event)} tran -> 1 dai dien [{rep['name']}]")

    final_matches = live_matches + final_upcoming
    for m in live_matches:
        m["team_a"] = clean_team_name(m.get("team_a", ""))
        m["team_b"] = clean_team_name(m.get("team_b", ""))
        m["name"] = f"{m['team_a']} vs {m['team_b']}"

    final_matches.sort(key=lambda m: (0 if m["is_live"] else 1, m["time_sort"]))
    print(f"  Cuoi cung: {len(final_matches)} tran ({len(live_matches)} LIVE + {len(final_upcoming)} SAP)")

    if live_matches:
        print(f"  Danh sach LIVE:")
        for m in live_matches:
            print(f"    - ID {m['match_id']}: {m['name']} | {m['time']}")

    return final_matches

# ─────────────────────────────────────────────────────────────────────────────
# BUILD CHANNEL
# ─────────────────────────────────────────────────────────────────────────────

def build_channel(match, thumb_url="", cate_type="billiards"):
    uid        = make_id(match["stream_url"], "chtv")
    src_id     = make_id(match["stream_url"], "src")
    ct_id      = make_id(match["stream_url"], "ct")
    st_id      = make_id(match["stream_url"], "st")
    lnk_id     = make_id(match["stream_url"], "lnk")

    label_text  = "LIVE" if match["is_live"] else "🕐 Sắp"
    label_color = "#ff4444" if match["is_live"] else "#aaaaaa"

    display_name = match["name"]
    if match["time"] and not match["is_live"]:
        display_name = f"{match['name']} | {match['time']}"
    if match["caster"]:
        display_name += f" | {match['caster']}"

    stream_links = [{
        "id": lnk_id, "name": match["caster"] or "Stream", "type": "hls", "default": True,
        "url": match["stream_url"],
        "request_headers": [
            {"key": "Referer", "value": HEADERS["Referer"]},
            {"key": "User-Agent", "value": FULL_UA},
        ],
    }]

    channel = {
        "id": uid, "name": display_name, "type": "single",
        "display": "thumbnail-only", "enable_detail": False,
        "labels": [{"text": label_text, "position": "top-left",
                    "color": "#00000080", "text_color": label_color}],
        "sources": [{
            "id": src_id, "name": "ChoangTV",
            "contents": [{
                "id": ct_id, "name": match["name"],
                "streams": [{"id": st_id, "name": "CHTV", "stream_links": stream_links}],
            }],
        }],
        "org_metadata": {
            "league": match.get("league", ""),
            "team_a": match.get("team_a", ""),
            "team_b": match.get("team_b", ""),
            "logo_a": match.get("logo_a", ""),
            "logo_b": match.get("logo_b", ""),
            "time": match.get("time_only", ""),
            "date": match.get("date_only", ""),
            "blv": match.get("caster", ""),
            "is_live": match["is_live"],
            "cate_type": cate_type,
        },
    }

    if thumb_url:
        channel["image"] = {
            "padding": 1, "background_color": "#ffffff",
            "display": "contain", "url": thumb_url,
            "width": 1600, "height": 1200,
        }
    return channel

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global SITE_URL, API_URL, HEADERS

    os.makedirs(THUMBS_DIR, exist_ok=True)
    cleanup_old_thumbs(days=3)

    print(f"Gio VN hien tai : {now_vn().strftime('%H:%M %d/%m/%Y')}")

    resolved_site, resolved_referer, resolved_api_domain = resolve_site_domain(ENTRY_SITE_URL, DEFAULT_DOMAIN)
    SITE_URL = resolved_site
    API_URL  = f"https://api.{resolved_api_domain}/matchSchedule/getList"
    HEADERS["Referer"] = resolved_referer

    print(f"  -> SITE_URL : {SITE_URL}")
    print(f"  -> API_URL  : {API_URL}")
    print(f"  -> Referer  : {HEADERS['Referer']}")
    print(f"  -> CDN_BASE : {CDN_BASE} (giu nguyen)")
    print("\nLay danh sach tran tu API...")

    matches = get_matches()
    live_count = sum(1 for m in matches if m["is_live"])
    print(f"\nTong: {len(matches)} | LIVE: {live_count} | Sap: {len(matches) - live_count}\n")

    billiard_channels = []
    vo_thuat_channels = []

    for i, match in enumerate(matches):
        status = "LIVE" if match["is_live"] else "SAP"
        print(f"[{status} {i+1}/{len(matches)}] {match['name']} ({match['time']}) | BLV: {match['caster']}")

        if not validate_stream(match["stream_url"], match):
            print(f"  ⚠️  Stream không hợp lệ, bỏ qua: {match['stream_url']}")
            continue

        uid        = make_id(match["stream_url"], "chtv")
        thumb_path = make_thumbnail(match, uid)
        cache_key  = match.get("logo_a", "") + match.get("logo_b", "") + THUMB_VER
        logo_hash  = hashlib.md5(cache_key.encode()).hexdigest()[:8]
        thumb_url  = f"{REPO_RAW}/{thumb_path}?v={logo_hash}" if REPO_RAW else ""

        # 🛡️ Phân loại bằng Regex word boundary - tránh bug Mohammad = MMA
        text_to_check = f"{match.get('name', '')} {match.get('league', '')} {match.get('category', '')}"
        is_billiard = bool(BILLIARD_PATTERN.search(text_to_check))
        is_martial  = bool(MARTIAL_PATTERN.search(text_to_check))
        cate_type   = "martial" if (is_martial and not is_billiard) else "billiards"

        ch = build_channel(match, thumb_url, cate_type)

        if cate_type == "martial":
            vo_thuat_channels.append(ch)
        else:
            billiard_channels.append(ch)

        time.sleep(0.2)

    groups = []
    if vo_thuat_channels:
        lc_ma = sum(1 for ch in vo_thuat_channels if ch.get("org_metadata", {}).get("is_live", False))
        groups.append({
            "id": "cate_vothuat",
            "name": f"🥊 Võ Thuật ({lc_ma} LIVE)" if lc_ma > 0 else "🥊 Võ Thuật",
            "display": "vertical", "grid_number": 2, "enable_detail": False,
            "channels": vo_thuat_channels,
        })

    if billiard_channels:
        lc_bi = sum(1 for ch in billiard_channels if ch.get("org_metadata", {}).get("is_live", False))
        groups.append({
            "id": "cate_billiards",
            "name": f"🎱 Billiards ({lc_bi} LIVE)" if lc_bi > 0 else "🎱 Billiards",
            "display": "vertical", "grid_number": 2, "enable_detail": False,
            "channels": billiard_channels,
        })

    output = {
        "id": "choangtv", "url": SITE_URL, "name": "ChoangTV",
        "color": "#a37ef2", "grid_number": 3,
        "image": {"type": "cover", "url": f"{SITE_URL}/__og-image__/image/og.png"},
        "groups": groups,
    }

    staging = "output_staging.json"
    with open(staging, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total = len(vo_thuat_channels) + len(billiard_channels)

    def normalize(path):
        try:
            with open(path, encoding="utf-8") as f: d = json.load(f)
            s = json.dumps(d, sort_keys=True, ensure_ascii=False)
            return re.sub(r"\?expire=\d+", "", s)
        except Exception: return ""

    if normalize("output.json") != normalize(staging):
        os.replace(staging, "output.json")
        print(f"\nXong! {total} kenh -> output.json (DA CAP NHAT)")
    else:
        os.remove(staging)
        print(f"\nXong! {total} kenh -> Khong co thay doi, giu nguyen output.json")

if __name__ == "__main__":
    main()
