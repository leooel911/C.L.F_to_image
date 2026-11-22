#!/usr/bin/env python3
"""
班表圖檔核心引擎：支援「班表原檔」自動辨識單位/職位、同單位同職位覆蓋、略過重複出圖、輸出至「班表圖檔」與郵件通知。
C.L.F DESIGNS UI/UX 7欄滿格對齊與郵件圖檔優化版 v4.24
"""

import base64
import glob
import hashlib
import io
import json
import os
import re
import shutil
import smtplib
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from email.header import Header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import matplotlib
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import pandas as pd

matplotlib.use("Agg")

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RAW_DIR = os.path.join(CURRENT_DIR, "班表原檔")
DEFAULT_OUT_DIR = os.path.join(CURRENT_DIR, "班表圖檔")
DEFAULT_TXT_PATH = os.path.join(CURRENT_DIR, "大班表資料.txt")
DEFAULT_STATE_PATH = os.path.join(CURRENT_DIR, ".render_cache.json")

DEFAULT_SENDER_EMAIL = "leooel911@gmail.com"
DEFAULT_SENDER_PASSWORD = "rytt mhwq fala jkzj"

DEFAULT_FRIEND_EMAILS = {
    "江立夫": "leo_chiang@thsrc.com.tw",
    "黃淨茵": "chingyin_huang@thsrc.com.tw",
    "李沛迪": "peter_lee@thsrc.com.tw",
    "耿瑞騏": "samurai_keng@thsrc.com.tw",
    "王婷禾": "linda_wang@thsrc.com.tw",
    "符湘君": "stacy_fu@thsrc.com.tw",
}

NATIONAL_HOLIDAYS = {
    "1/1": "元旦",
    "2/16": "除夕", "2/17": "春節", "2/18": "初二", "2/19": "初三", "2/20": "初四",
    "2/28": "和平紀念日",
    "4/3": "兒童節", "4/4": "清明節",
    "5/1": "勞動節",
    "6/19": "端午節",
    "9/25": "中秋節",
    "9/28": "教師節",
    "10/10": "國慶日",
    "10/25": "台灣光復節",
    "12/25": "行憲紀念日",
}

TRANSPORT_PERIODS = {
    "9/24-9/29": "中秋疏運",
    "10/4-10/10": "雙十節疏運",
    "10/25-10/31": "光復節疏運",
}

TITLE = "//    T r a i n    c r e w    D U T Y    C A L E N D A R"

POS_KEYWORDS = {
    "TD": ["TD", "駕駛"],
    "TM": ["TM", "列車長"],
    "TA": ["TA", "服勤員"],
}

UNIT_KEYWORDS = {
    "TTN": ["TTN", "北區", "南港", "台北"],
    "TTC": ["TTC", "中區", "台中"],
    "TTS": ["TTS", "南區", "左營", "台南"],
}

C_HDR, C_BORDER, C_EMPTY = "#0F172A", "#94A3B8", "#F1F5F9"
C_WORK_BG = "#FFFFFF"
C_WEEKEND_BG = "#F8FAFC"
C_DO_BG = "#FFE4E6"
C_PAY_BG = "#FFEDD5"
C_TOWN_BG = "#CBD5E1"
C_TIME, C_TRAIN, C_DO_TXT, C_PAY_TXT, C_HOLI_TXT, C_OT_TXT, C_NOTE_TXT = (
    "#0F172A", "#020617", "#9F1239", "#C2410C", "#9A3412", "#B91C1C", "#5B21B6"
)
C_TOWN_TXT = "#1E293B"


def normalize_name(name: str) -> str:
    if not name:
        return ""
    return re.sub(r"[\s\u3000\xa0]+", "", str(name)).strip()


def resolve_roster_file(base_dir: str) -> str:
    for fname in ("發送名單", "發送名單.txt"):
        p = os.path.join(base_dir, fname)
        if os.path.exists(p) and os.path.isfile(p):
            return p
    return os.path.join(base_dir, "發送名單")


def load_friend_emails(base_dir: str = CURRENT_DIR) -> dict:
    merged = dict(DEFAULT_FRIEND_EMAILS)
    rpath = resolve_roster_file(base_dir)
    if os.path.exists(rpath):
        try:
            with open(rpath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip().rstrip(",")
                    if not s or s.startswith("#") or s.startswith("//"):
                        continue
                    m = re.match(r'^"?([^":]+?)"?\s*:\s*"?([^"]+)"?$', s)
                    if m:
                        person = normalize_name(m.group(1))
                        email = m.group(2).strip().strip('"').strip("'")
                        if person and "@" in email:
                            merged[person] = email
        except OSError:
            pass
    return merged


def append_or_update_friend_email(name: str, email: str, base_dir: str = CURRENT_DIR) -> tuple[bool, str]:
    clean_n = normalize_name(name)
    clean_e = (email or "").strip()
    if not clean_n or "@" not in clean_e or "." not in clean_e.split("@")[-1]:
        return False, "請輸入有效的姓名與電子信箱格式。"

    rpath = resolve_roster_file(base_dir)
    os.makedirs(os.path.dirname(rpath), exist_ok=True)
    lines = []
    if os.path.exists(rpath):
        with open(rpath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()

    updated = False
    new_lines = []
    for line in lines:
        s = line.strip().rstrip(",")
        m = re.match(r'^"?([^":]+?)"?\s*:\s*"?([^"]+)"?$', s)
        if m and normalize_name(m.group(1)) == clean_n:
            new_lines.append(f'"{clean_n}": "{clean_e}",')
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f'"{clean_n}": "{clean_e}",')

    with open(rpath, "w", encoding="utf-8") as f:
        f.write("\n".join([l for l in new_lines if l is not None]).strip() + "\n")

    return True, f"已{'更新' if updated else '新增'}【{clean_n}】至發送名單。"


def detect_unit_and_pos(filepath: str, head_text: str = "") -> tuple[str, str]:
    fname_upper = os.path.basename(filepath).upper()
    combined_upper = f"{fname_upper} {os.path.dirname(filepath).upper()} {head_text.upper()}"

    unit = "TTN"
    for u_code, kws in UNIT_KEYWORDS.items():
        if any(kw.upper() in fname_upper for kw in kws):
            unit = u_code
            break
    else:
        for u_code, kws in UNIT_KEYWORDS.items():
            if any(kw.upper() in combined_upper for kw in kws):
                unit = u_code
                break

    pos = "ALL"
    for p_code, kws in POS_KEYWORDS.items():
        if any(re.search(rf"(?<![A-Z0-9]){re.escape(kw.upper())}(?![A-Z0-9])", fname_upper) or kw.upper() in fname_upper for kw in kws):
            pos = p_code
            break
    else:
        for p_code, kws in POS_KEYWORDS.items():
            if any(kw.upper() in combined_upper for kw in kws):
                pos = p_code
                break

    return unit, pos


def read_schedule_dataframe(file_path: str) -> tuple[pd.DataFrame, str, str]:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        try:
            df = pd.read_csv(file_path, header=None, encoding="utf-8-sig")
        except Exception:
            df = pd.read_csv(file_path, header=None, encoding="big5")
    else:
        df = pd.read_excel(file_path, header=None)

    head_sample = " ".join(
        str(x) for x in df.iloc[: min(6, len(df)), : min(8, df.shape[1])].fillna("").values.flatten()
    )
    unit, pos = detect_unit_and_pos(file_path, head_sample)
    return df, unit, pos


def save_uploaded_roster_overwrite(
    src_file_path: str,
    original_filename: str,
    raw_dir: str = DEFAULT_RAW_DIR,
) -> tuple[str, str, str, bool]:
    os.makedirs(raw_dir, exist_ok=True)
    _, unit, pos = read_schedule_dataframe(src_file_path)
    ext = os.path.splitext(original_filename)[1].lower()
    if ext not in (".xls", ".xlsx", ".csv"):
        ext = os.path.splitext(src_file_path)[1].lower() or ".xlsx"

    canonical_name = f"{unit}_{pos}{ext}"
    dest_path = os.path.join(raw_dir, canonical_name)

    existed = False
    for old_fp in glob.glob(os.path.join(raw_dir, f"{unit}_{pos}.*")):
        if os.path.abspath(old_fp) != os.path.abspath(dest_path):
            os.remove(old_fp)
            existed = True
    if os.path.exists(dest_path):
        existed = True

    shutil.copy2(src_file_path, dest_path)
    return dest_path, unit, pos, existed


def list_stored_rosters(raw_dir: str = DEFAULT_RAW_DIR) -> list[dict]:
    items = []
    if not raw_dir or not os.path.isdir(raw_dir):
        return items
    for ext in ("*.xlsx", "*.xls", "*.csv"):
        for fp in glob.glob(os.path.join(raw_dir, ext)):
            bn = os.path.basename(fp)
            if bn.startswith("~$") or bn == ".DS_Store":
                continue
            unit, pos = detect_unit_and_pos(fp, "")
            mtime = datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M")
            items.append({
                "filename": bn,
                "path": fp,
                "unit": unit,
                "pos": pos,
                "updated_at": mtime,
            })
    items.sort(key=lambda x: (x["unit"], x["pos"], x["filename"]))
    return items


def sync_file_to_github(
    local_file_path: str,
    repo_rel_path: str,
    github_token: str,
    github_repo: str,
    branch: str = "main",
) -> tuple[bool, str]:
    if not github_token or not github_repo:
        return False, "未設定 GITHUB_TOKEN 或 GITHUB_REPO，僅更新目前容器後台。"
    if not os.path.exists(local_file_path):
        return False, f"找不到本機檔案：{local_file_path}"

    api_url = f"https://api.github.com/repos/{github_repo}/contents/{repo_rel_path}"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "CLF-Duty-Scheduler-V2",
    }

    sha = None
    get_req = urllib.request.Request(f"{api_url}?ref={branch}", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(get_req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            sha = data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return False, f"查詢 GitHub 檔案失敗（HTTP {e.code}）"
    except Exception as e:
        return False, f"連線 GitHub 失敗：{e}"

    with open(local_file_path, "rb") as f:
        b64_content = base64.b64encode(f.read()).decode("ascii")

    payload = {
        "message": f"Update roster baseline: {repo_rel_path}",
        "content": b64_content,
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    put_req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(put_req, timeout=20) as resp:
            if resp.status in (200, 201):
                return True, f"已同步覆蓋至 GitHub `{repo_rel_path}`"
            return False, f"GitHub 更新回應異常（HTTP {resp.status}）"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        return False, f"GitHub 寫入失敗（HTTP {e.code}）：{err_body[:160]}"
    except Exception as e:
        return False, f"GitHub 同步發生錯誤：{e}"


def parse_transport_periods(raw_periods: dict, year: int = 2026) -> dict:
    expanded = {}
    for k, v in raw_periods.items():
        key_str = str(k).strip()
        if "-" in key_str:
            parts = key_str.split("-", 1)
            s_m, s_d = map(int, parts[0].strip().split("/"))
            e_m, e_d = map(int, parts[1].strip().split("/"))
            start_year = year
            end_year = year + 1 if (s_m >= 11 and e_m <= 2) else year
            cur = date(start_year, s_m, s_d)
            end_dt = date(end_year, e_m, e_d)
            while cur <= end_dt:
                expanded[f"{cur.month}/{cur.day}"] = v
                cur += timedelta(days=1)
        else:
            expanded[key_str] = v
    return expanded


def pad_time(t_str: str) -> str:
    if not t_str or ":" not in t_str:
        return t_str
    parts = t_str.split(":", 1)
    if parts[0].strip().isdigit() and parts[1].strip().isdigit():
        return f"{int(parts[0].strip()):02d}:{int(parts[1].strip()):02d}"
    return t_str


def fmt_hours(h: str) -> str:
    if not h:
        return ""
    s = str(h).strip()
    if "h" in s.lower():
        return s
    if ":" in s:
        p = s.split(":", 1)
        return f"{p[0].strip()}h{p[1].strip()}m"
    return s


def parse_cell(raw) -> dict:
    if raw is None:
        return dict(start="", train="", end="", hours="", note="")
    lines = [line.strip() for line in str(raw).strip().splitlines() if line.strip()]
    if not lines:
        return dict(start="", train="", end="", hours="", note="")

    if len(lines) == 1 and ("DO" in lines[0] or "D2W" in lines[0]):
        return dict(start="", train=lines[0], end="", hours="", note="")

    time_pattern = re.compile(r"^\d{1,2}:\d{2}$")

    if "PAY" in lines:
        times = [line for line in lines if time_pattern.match(line)]
        start_t = pad_time(times[0]) if len(times) >= 1 else ""
        end_t = pad_time(times[1]) if len(times) >= 2 else ""
        hours = ""
        for line in reversed(lines):
            if "h" in line.lower() or (":" in line and line not in times):
                hours = fmt_hours(line)
                break
        return dict(start=start_t, train="PAY", end=end_t, hours=hours, note="")

    times = []
    other_lines = []
    hours = ""
    for line in lines:
        if time_pattern.match(line):
            times.append(line)
        elif re.search(r"\d+\s*h", line, re.IGNORECASE):
            hours = fmt_hours(line)
        else:
            other_lines.append(line)

    if len(times) >= 3 and not hours:
        hours = fmt_hours(times.pop())

    start_time = pad_time(times[0]) if len(times) >= 1 else ""
    end_time = pad_time(times[1]) if len(times) >= 2 else ""

    train_code = ""
    notes = []
    for item in other_lines:
        if item.startswith("DO") or item.startswith("D2W"):
            notes.append(item)
        elif not train_code:
            train_code = item
        else:
            notes.append(item)

    return dict(start=start_time, end=end_time, train=train_code, hours=hours, note=" ".join(notes))


def detect_base_year(df: pd.DataFrame, source_label: str = "") -> int:
    head_text = ""
    if df is not None and not df.empty:
        head_text = " ".join(
            str(x) for x in df.iloc[: min(4, len(df)), : min(6, df.shape[1])].fillna("").values.flatten()
        )
    yr_match = re.search(r"(20\d{2})\s*年", f"{source_label} {head_text}")
    if yr_match:
        return int(yr_match.group(1))
    return datetime.now().year


def parse_employees_from_dataframe(
    df: pd.DataFrame,
    target_names: list[str],
    unit: str = "TTN",
    pos: str = "ALL",
    source_label: str = "",
) -> tuple[list[dict], list[str]]:
    clean_targets = [normalize_name(n) for n in target_names if normalize_name(n)]
    base_year = detect_base_year(df, source_label=source_label)
    logs = []

    header_blocks: list[tuple[int, dict[int, date], list[date]]] = []
    for r_idx, row in df.iterrows():
        vals = ["" if pd.isna(v) else str(v) for v in row.tolist()]
        if not any(v.strip() for v in vals):
            continue
        first_cell = vals[0].strip()
        if any(k in first_cell for k in ["員工", "編號", "工號", "Emp", "No"]):
            col_dates: dict[int, date] = {}
            found_dates: list[date] = []
            for c_idx in range(2, len(vals)):
                m_date = re.search(r"(\d{1,2}/\d{1,2})", vals[c_idx].strip())
                if m_date:
                    s_m, s_d = map(int, m_date.group(1).split("/"))
                    dt_val = date(base_year, s_m, s_d)
                    if found_dates and s_m < found_dates[-1].month:
                        dt_val = date(base_year + 1, s_m, s_d)
                    col_dates[c_idx] = dt_val
                    found_dates.append(dt_val)
            if found_dates:
                header_blocks.append((r_idx, col_dates, found_dates))
                logs.append(f"偵測到班表起始日期：{found_dates[0].month}/{found_dates[0].day} ({unit}/{pos})")

    emp_found: dict[str, dict] = {}
    for b_idx, (h_row_idx, col_dates, found_dates) in enumerate(header_blocks):
        next_h_row = header_blocks[b_idx + 1][0] if b_idx + 1 < len(header_blocks) else len(df)
        for r_idx in range(h_row_idx + 1, next_h_row):
            vals = ["" if pd.isna(v) else str(v) for v in df.iloc[r_idx].tolist()]
            if not vals:
                continue
            match = re.search(r"([A-Z]?\d{5,8})", vals[0].strip())
            if not match or len(vals) < 2:
                continue
            emp_id = match.group(1)
            emp_name = normalize_name(vals[1])
            if emp_name in clean_targets and emp_name not in emp_found:
                emp_dates = [f"{d.month}/{d.day}" for d in found_dates]
                emp_cells = [vals[c_idx] if c_idx < len(vals) else "" for c_idx in sorted(col_dates.keys())]
                start_dt = found_dates[0]
                fingerprint = hashlib.md5(
                    f"{emp_id}|{unit}|{pos}|{','.join(emp_dates)}|{'||'.join(str(c) for c in emp_cells)}".encode("utf-8")
                ).hexdigest()
                emp_found[emp_name] = {
                    "id": emp_id,
                    "name": emp_name,
                    "unit": unit,
                    "pos": pos,
                    "source": source_label,
                    "cells": emp_cells,
                    "start_dt": start_dt,
                    "dates": emp_dates,
                    "file_date_str": f"{start_dt.month}/{start_dt.day}",
                    "fingerprint": fingerprint,
                }

    results = [emp_found[n] for n in clean_targets if n in emp_found]
    return results, logs


def collect_employees_from_sources(raw_dir: str, txt_path: str, target_names: list[str]) -> tuple[list[dict], list[str]]:
    logs = []
    collected = []
    seen_names = set()

    raw_files = []
    if raw_dir and os.path.isdir(raw_dir):
        for ext in ("*.xlsx", "*.xls", "*.csv"):
            for fp in glob.glob(os.path.join(raw_dir, "**", ext), recursive=True):
                bn = os.path.basename(fp)
                if not bn.startswith("~$") and bn != ".DS_Store":
                    raw_files.append(fp)

    raw_files.sort(key=lambda p: (-os.path.getmtime(p), os.path.basename(p)))
    logs.append(f"讀取後台班表原檔目錄：{raw_dir}（共 {len(raw_files)} 個底稿檔案）")

    for fp in raw_files:
        remaining = [n for n in target_names if normalize_name(n) not in seen_names]
        if not remaining:
            break
        try:
            df, unit, pos = read_schedule_dataframe(fp)
            found, f_logs = parse_employees_from_dataframe(
                df, remaining, unit=unit, pos=pos, source_label=os.path.basename(fp)
            )
            logs.extend(f_logs)
            for item in found:
                if item["name"] not in seen_names:
                    collected.append(item)
                    seen_names.add(item["name"])
                    logs.append(f"命中【{item['name']}】← 後台底稿 {os.path.basename(fp)} ({unit}/{pos})")
        except Exception as e:
            logs.append(f"解析檔案失敗 {os.path.basename(fp)}：{e}")

    remaining = [n for n in target_names if normalize_name(n) not in seen_names]
    if remaining and txt_path and os.path.exists(txt_path):
        with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
            txt_content = f.read()
        if txt_content.strip():
            txt_df = pd.read_csv(io.StringIO(txt_content.strip()), sep="\t", header=None)
            found, _ = parse_employees_from_dataframe(
                txt_df, remaining, unit="TTN", pos="ALL", source_label=os.path.basename(txt_path)
            )
            for item in found:
                if item["name"] not in seen_names:
                    collected.append(item)
                    seen_names.add(item["name"])
                    logs.append(f"命中【{item['name']}】← {os.path.basename(txt_path)}（備援文字檔）")

    missing = [n for n in target_names if normalize_name(n) not in seen_names]
    if missing:
        logs.append(f"尚未找到班表資料：{', '.join(missing)}")

    return collected, logs


def is_overtime(h: str) -> bool:
    if not h:
        return False
    try:
        cleaned = str(h).lower().replace("h", ":").replace("m", "").strip()
        parts = cleaned.split(":", 1)
        hours = int(parts[0].strip())
        minutes = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip() else 0
        return (hours * 60 + minutes) > 510
    except Exception:
        return False


def is_town_shift(tr: str, note: str) -> bool:
    target_keywords = ["TOWN", "STD", "TTN", "DTT", "工廠", "回廠", "訓練"]
    combined = f"{tr or ''} {note or ''}".upper()
    return any(kw in combined for kw in target_keywords)


def build_weeks(start_dt: date, dates: list, cells: list) -> list:
    first_wd = (start_dt.weekday() + 1) % 7
    weeks, week = [], [None] * first_wd
    for dt_label, raw in zip(dates, cells):
        week.append((dt_label, parse_cell(raw)))
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        while len(week) < 7:
            week.append(None)
        weeks.append(week)
    return weeks


def setup_font():
    font_paths = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:\\Windows\\Fonts\\msjh.ttc",
        "C:\\Windows\\Fonts\\msjhbd.ttc",
    ]
    for root_dir in ("/usr/share/fonts", "/usr/local/share/fonts"):
        if os.path.isdir(root_dir):
            for dirpath, _, filenames in os.walk(root_dir):
                for fn in sorted(filenames):
                    low = fn.lower()
                    if ("cjk" in low or "noto" in low or "pingfang" in low or "msjh" in low) and low.endswith((".ttc", ".ttf", ".otf")):
                        full = os.path.join(dirpath, fn)
                        if full not in font_paths:
                            font_paths.append(full)
    for path in font_paths:
        if os.path.exists(path):
            try:
                fm.fontManager.addfont(path)
                return fm.FontProperties(fname=path)
            except Exception:
                continue
    return None


def load_cache(cache_path: str = DEFAULT_STATE_PATH) -> dict:
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache_data: dict, cache_path: str = DEFAULT_STATE_PATH):
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)


def render_employee_image(emp: dict, out_dir: str, font_prop=None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    emp_id = emp["id"]
    emp_name = emp["name"]
    unit = emp.get("unit", "TTN")
    cells = emp["cells"]
    start_dt = emp["start_dt"]
    dates = emp["dates"]
    file_date_str = emp["file_date_str"]

    def fp(size=9, bold=False):
        if font_prop:
            return fm.FontProperties(fname=font_prop.get_file(), size=size, weight="bold" if bold else "normal")
        return fm.FontProperties(size=size, weight="bold" if bold else "normal")

    active_transport = parse_transport_periods(TRANSPORT_PERIODS, year=start_dt.year if start_dt else 2026)
    weeks = build_weeks(start_dt, dates, cells)
    n_weeks = len(weeks)

    out_name = f"{unit}班表_{emp_name}_{file_date_str.replace('/', '-')}_起.png"
    out_path = os.path.join(out_dir, out_name)

    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    ML, MR, MT, MB, TH, DH = 0.015, 0.015, 0.015, 0.065, 0.09, 0.055
    TW = 1.0 - ML - MR
    CW = TW / 7
    RH = (1.0 - MT - MB - TH - DH) / n_weeks

    ty = 1.0 - MT - TH
    ax.add_patch(FancyBboxPatch((ML, ty), TW, TH, boxstyle="square,pad=0", linewidth=0, facecolor=C_HDR))
    ax.text(ML + 0.008, ty + TH * 0.58, TITLE, ha="left", va="center", color="#FFFFFF", fontproperties=fp(12, True))
    ax.text(
        ML + 0.008, ty + TH * 0.25,
        f"CREW ID // {emp_id}    OPERATOR // {emp_name}    TIMELINE // {dates[0]} ~ {dates[-1]} ({len(dates)} DAYS)",
        ha="left", va="center", color="#CBD5E1", fontproperties=fp(9, True),
    )

    bw = 0.165
    bx = 1.0 - MR - bw
    ax.add_patch(FancyBboxPatch((bx, ty + TH * 0.32), bw, TH * 0.38, boxstyle="square,pad=0", linewidth=0, facecolor="#1E293B"))
    ax.text(bx + 0.012, ty + TH * 0.32 + (TH * 0.38) / 2, "●", ha="center", va="center", color="#22C55E", fontproperties=fp(7))
    ax.text(
        bx + 0.024, ty + TH * 0.32 + (TH * 0.38) / 2,
        "C . L . F    D E S I G N S", ha="left", va="center", color="#F8FAFC", fontproperties=fp(7.5, True),
    )

    dlabels = ["SUN 星期日", "MON 星期一", "TUE 星期二", "WED 星期三", "THU 星期四", "FRI 星期五", "SAT 星期六"]
    dy = ty - DH
    for c in range(7):
        x = ML + c * CW
        ax.add_patch(FancyBboxPatch((x, dy), CW, DH, boxstyle="square,pad=0", linewidth=0.8, edgecolor=C_BORDER, facecolor="#CBD5E1"))
        ax.text(x + CW / 2, dy + DH / 2, dlabels[c], ha="center", va="center", color="#0F172A", fontproperties=fp(9, True))

    has_emp_do = False
    has_emp_pay = False
    has_emp_ot = False
    has_emp_town = False
    for week in weeks:
        for cell in week:
            if cell is not None:
                _, d = cell
                tr = d["train"]
                note = d.get("note", "")
                hours = d.get("hours", "")
                is_holiday = "D2W" in tr or "DO2W" in tr or "D2W" in note or "DO2W" in note
                if is_holiday or tr.startswith("DO"):
                    has_emp_do = True
                elif tr == "PAY":
                    has_emp_pay = True
                elif is_town_shift(tr, note):
                    has_emp_town = True
                if is_overtime(hours):
                    has_emp_ot = True

    for ri, week in enumerate(weeks):
        ry = dy - (ri + 1) * RH
        for ci, cell in enumerate(week):
            x = ML + ci * CW
            if cell is None:
                ax.add_patch(FancyBboxPatch((x, ry), CW, RH, boxstyle="square,pad=0", linewidth=0.8, edgecolor=C_BORDER, facecolor=C_EMPTY))
                continue

            dt_str, d = cell
            tr = d["train"]
            note = d.get("note", "")
            is_national_holiday = dt_str in NATIONAL_HOLIDAYS
            is_holiday = "D2W" in tr or "DO2W" in tr or "D2W" in note or "DO2W" in note

            if is_holiday or tr.startswith("DO"):
                bg = C_DO_BG
            elif tr == "PAY":
                bg = C_PAY_BG
            elif is_town_shift(tr, note):
                bg = C_TOWN_BG
            elif ci == 0 or ci == 6:
                bg = C_WEEKEND_BG
            else:
                bg = C_WORK_BG

            ax.add_patch(FancyBboxPatch((x, ry), CW, RH, boxstyle="square,pad=0", linewidth=0.8, edgecolor=C_BORDER, facecolor=bg))

            if is_national_holiday:
                ax.text(x + 0.005, ry + RH - 0.004, f"{dt_str} ({NATIONAL_HOLIDAYS[dt_str]})", ha="left", va="top", color=C_HOLI_TXT, fontproperties=fp(8, True))
            else:
                ax.text(x + 0.005, ry + RH - 0.004, dt_str, ha="left", va="top", color=C_TIME, fontproperties=fp(9, True))

            if dt_str in active_transport:
                ax.text(x + CW - 0.005, ry + RH - 0.004, active_transport[dt_str], ha="right", va="top", color=C_NOTE_TXT, fontproperties=fp(7.5, True))

            if d.get("hours"):
                ot = is_overtime(d["hours"])
                ax.text(x + CW - 0.004, ry + 0.003, f"({d['hours']})", ha="right", va="bottom", color=C_OT_TXT if ot else "#334155", fontproperties=fp(7.5, ot))

            cx = x + CW / 2
            cy_center = ry + RH * 0.48

            if tr.startswith("DO"):
                if note:
                    ax.text(cx, ry + RH * 0.62, note, ha="center", va="center", color=C_NOTE_TXT, fontproperties=fp(8.5, True))
                    ax.text(cx, ry + RH * 0.35, tr, ha="center", va="center", color=C_DO_TXT, fontproperties=fp(11.5, True))
                else:
                    ax.text(cx, cy_center, tr, ha="center", va="center", color=C_DO_TXT, fontproperties=fp(13.0, True))
            elif tr == "PAY":
                ax.text(cx, ry + RH * 0.62, "特休 PAY", ha="center", va="center", color=C_PAY_TXT, fontproperties=fp(11.5, True))
                if d["start"] and d["end"]:
                    ax.text(cx, ry + RH * 0.35, f"{d['start']}～{d['end']}", ha="center", va="center", color=C_TIME, fontproperties=fp(10.0, True))
            else:
                if note:
                    ax.text(cx, ry + RH * 0.80, note, ha="center", va="center", color=C_NOTE_TXT, fontproperties=fp(8.5, True))
                    ax.text(cx, ry + RH * 0.58, d["start"], ha="center", va="center", color=C_TIME, fontproperties=fp(12.0, True))
                    ax.text(cx, ry + RH * 0.38, d["end"], ha="center", va="center", color=C_TIME, fontproperties=fp(12.0, True))
                    ax.text(cx, ry + RH * 0.18, tr, ha="center", va="center", color=C_TRAIN, fontproperties=fp(11.0, True))
                else:
                    ax.text(cx, ry + RH * 0.68, d["start"], ha="center", va="center", color=C_TIME, fontproperties=fp(12.5, True))
                    ax.text(cx, ry + RH * 0.44, d["end"], ha="center", va="center", color=C_TIME, fontproperties=fp(12.5, True))
                    ax.text(cx, ry + RH * 0.20, tr, ha="center", va="center", color=C_TRAIN, fontproperties=fp(11.5, True))

    legend_y = MB * 0.42
    badge_w, badge_h = CW * 0.90, 0.026
    has_active_transport = any(d in active_transport for d in dates)
    has_active_holiday = any(d in NATIONAL_HOLIDAYS for d in dates)

    pill_legends = [
        (0, "#F1F5F9", "#94A3B8", C_NOTE_TXT, "備註 (Note)"),
        (1, C_DO_BG if has_emp_do else C_WORK_BG, "#FDA4AF" if has_emp_do else "#CBD5E1", C_DO_TXT if has_emp_do else "#94A3B8", "休假日 (DO)"),
        (2, C_PAY_BG if has_emp_pay else C_WORK_BG, "#FDBA74" if has_emp_pay else "#CBD5E1", C_PAY_TXT if has_emp_pay else "#94A3B8", "特休 (PAY)"),
        (3, C_WORK_BG, "#FCA5A5" if has_emp_ot else "#CBD5E1", C_OT_TXT if has_emp_ot else "#94A3B8", "工時 > 8.5h"),
        (4, "#FFF7ED" if has_active_holiday else C_WORK_BG, "#FED7AA" if has_active_holiday else "#CBD5E1", C_HOLI_TXT if has_active_holiday else "#94A3B8", "國定假日"),
        (5, "#F3E8FF" if has_active_transport else C_WORK_BG, "#DDD6FE" if has_active_transport else "#CBD5E1", C_NOTE_TXT if has_active_transport else "#94A3B8", "疏運"),
        (6, C_TOWN_BG if has_emp_town else C_WORK_BG, "#64748B" if has_emp_town else "#CBD5E1", C_TOWN_TXT if has_emp_town else "#94A3B8", "非正線勤務"),
    ]

    for col_idx, bg_clr, border_clr, txt_clr, label in pill_legends:
        col_x = ML + col_idx * CW
        lx = col_x + (CW - badge_w) / 2
        badge = FancyBboxPatch(
            (lx, legend_y), badge_w, badge_h,
            boxstyle="round,pad=0.002,rounding_size=0.008",
            linewidth=1.0, edgecolor=border_clr, facecolor=bg_clr,
        )
        ax.add_patch(badge)
        ax.text(lx + badge_w / 2, legend_y + badge_h / 2, label, ha="center", va="center", color=txt_clr, fontproperties=fp(7.5, True))

    footer_y = MB * 0.15
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    ax.text(ML + 0.005, footer_y, "DESIGNED BY: C.L.F // TECHNICAL SHIFT SYSTEM v4.24", ha="left", va="center", color="#64748B", fontproperties=fp(7.5, True))
    ax.text(1.0 - MR - 0.005, footer_y, f"GENERATED: {current_time_str} | CONFIDENTIAL", ha="right", va="center", color="#64748B", fontproperties=fp(7.5))

    plt.tight_layout(pad=0)
    plt.savefig(out_path, format="png", dpi=400, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def run_batch_render(
    target_names: list[str],
    raw_dir: str = DEFAULT_RAW_DIR,
    out_dir: str = DEFAULT_OUT_DIR,
    txt_path: str = DEFAULT_TXT_PATH,
    skip_unchanged: bool = True,
    force_regenerate: bool = False,
) -> tuple[list[dict], list[str]]:
    font_prop = setup_font()
    cache = load_cache()
    employees, logs = collect_employees_from_sources(raw_dir, txt_path, target_names)
    records = []

    for emp in employees:
        name = emp["name"]
        fp_hash = emp["fingerprint"]
        expected_name = f"{emp.get('unit', 'TTN')}班表_{name}_{emp['file_date_str'].replace('/', '-')}_起.png"
        expected_path = os.path.join(out_dir, expected_name)

        prev = cache.get(name, {})
        if skip_unchanged and not force_regenerate and prev.get("fingerprint") == fp_hash and os.path.exists(expected_path):
            logs.append(f"略過【{name}】：班表未變動且圖片已存在（{expected_name}）")
            records.append({
                "name": name,
                "file_date_str": emp["file_date_str"],
                "image_path": expected_path,
                "is_new": False,
                "status": "skipped",
            })
            continue

        out_path = render_employee_image(emp, out_dir=out_dir, font_prop=font_prop)
        cache[name] = {"fingerprint": fp_hash, "image_path": out_path, "updated_at": datetime.now().isoformat()}
        logs.append(f"產出【{name}】班表圖：{out_path}")
        records.append({
            "name": name,
            "file_date_str": emp["file_date_str"],
            "image_path": out_path,
            "is_new": True,
            "status": "generated",
        })

    save_cache(cache)
    return records, logs


def send_email_notification(
    file_path: str,
    emp_name: str,
    file_date_str: str,
    friend_emails: dict,
    sender_email: str = DEFAULT_SENDER_EMAIL,
    sender_password: str = DEFAULT_SENDER_PASSWORD,
) -> tuple[bool, str]:
    clean_name = normalize_name(emp_name)
    recipient = friend_emails.get(clean_name, "")
    if not recipient:
        return False, f"找不到【{clean_name}】的電子信箱設定。"
    if not os.path.exists(file_path):
        return False, f"找不到附件圖片：{file_path}"

    try:
        msg = MIMEMultipart()
        msg["From"] = sender_email
        msg["To"] = recipient
        msg["Subject"] = Header(f"【C.L.F 勤務班表】{clean_name} ({file_date_str} 起) 班表通知", "utf-8")

        body = (
            f"Hi {clean_name}：\n\n"
            f"這是系統自動為您繪製的勤務班表圖片（{file_date_str} 起），請查收附件。"
            f"如有換班需求，請參考輔助系統（測試中）:https://dutyengine.streamlit.app/。by L.F"
        )
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with open(file_path, "rb") as f:
            img_data = f.read()

        filename = os.path.basename(file_path)
        img_attachment = MIMEImage(img_data, name=filename)
        img_attachment.add_header("Content-Disposition", "attachment", filename=Header(filename, "utf-8").encode())
        msg.attach(img_attachment)

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient, msg.as_string())
        server.quit()
        return True, f"已寄送至【{clean_name}】({recipient})"
    except Exception as e:
        return False, f"寄送【{clean_name}】失敗：{e}"


def extract_all_employees_from_sources(raw_dir: str = DEFAULT_RAW_DIR, txt_path: str = DEFAULT_TXT_PATH) -> list[dict]:
    seen = set()
    roster = []

    raw_files = []
    if raw_dir and os.path.isdir(raw_dir):
        for ext in ("*.xlsx", "*.xls", "*.csv"):
            for fp in glob.glob(os.path.join(raw_dir, "**", ext), recursive=True):
                bn = os.path.basename(fp)
                if not bn.startswith("~$") and bn != ".DS_Store":
                    raw_files.append(fp)

    raw_files.sort(key=lambda p: (-os.path.getmtime(p), os.path.basename(p)))

    for fp in raw_files:
        try:
            df, unit, pos = read_schedule_dataframe(fp)
            for _, row in df.iterrows():
                vals = ["" if pd.isna(v) else str(v) for v in row.tolist()]
                if len(vals) < 2:
                    continue
                if re.search(r"^[A-Z]?\d{5,8}$", vals[0].strip()):
                    emp_name = normalize_name(vals[1])
                    if emp_name and emp_name not in seen and not any(k in emp_name for k in ["姓名", "員工", "Name"]):
                        seen.add(emp_name)
                        roster.append({
                            "name": emp_name,
                            "emp_id": vals[0].strip(),
                            "unit": unit,
                            "pos": pos,
                            "source": os.path.basename(fp),
                        })
        except Exception:
            continue

    if txt_path and os.path.exists(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                txt_content = f.read().strip()
            if txt_content:
                txt_df = pd.read_csv(io.StringIO(txt_content), sep="\t", header=None)
                for _, row in txt_df.iterrows():
                    vals = ["" if pd.isna(v) else str(v) for v in row.tolist()]
                    if len(vals) >= 2 and re.search(r"^[A-Z]?\d{5,8}$", vals[0].strip()):
                        emp_name = normalize_name(vals[1])
                        if emp_name and emp_name not in seen:
                            seen.add(emp_name)
                            roster.append({
                                "name": emp_name,
                                "emp_id": vals[0].strip(),
                                "unit": "TTN",
                                "pos": "ALL",
                                "source": os.path.basename(txt_path),
                            })
        except Exception:
            pass

    return roster


def remove_friend_email(name: str, base_dir: str = CURRENT_DIR) -> tuple[bool, str]:
    clean_n = normalize_name(name)
    if not clean_n:
        return False, "請選擇要刪除的人員。"

    rpath = resolve_roster_file(base_dir)
    os.makedirs(os.path.dirname(rpath), exist_ok=True)
    lines = []
    if os.path.exists(rpath):
        with open(rpath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()

    kept_lines = []
    removed_from_file = False
    for line in lines:
        s = line.strip().rstrip(",")
        m = re.match(r'^"?([^":]+?)"?\s*:\s*"?([^"]+)"?$', s)
        if m and normalize_name(m.group(1)) == clean_n:
            removed_from_file = True
            continue
        kept_lines.append(line)

    if clean_n in DEFAULT_FRIEND_EMAILS and not removed_from_file:
        kept_lines.append(f'"{clean_n}": "",')
        removed_from_file = True

    with open(rpath, "w", encoding="utf-8") as f:
        f.write("\n".join([l for l in kept_lines if l is not None]).strip() + ("\n" if kept_lines else ""))

    if removed_from_file:
        return True, f"已從發送名單移除【{clean_n}】。"
    return False, f"發送名單中找不到【{clean_n}】。"
