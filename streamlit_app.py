#!/usr/bin/env python3
"""
C.L.F 勤務班表出圖與郵件通知系統（雲端／手機操作版 V2）
"""

from datetime import datetime
import hmac
import io
import json
import os
import re
import tempfile
import zipfile

import streamlit as st

from schedule_plotter import (
    CURRENT_DIR,
    DEFAULT_OUT_DIR,
    DEFAULT_RAW_DIR,
    DEFAULT_SENDER_EMAIL,
    DEFAULT_SENDER_PASSWORD,
    DEFAULT_TXT_PATH,
    append_or_update_friend_email,
    extract_all_employees_from_sources,
    list_stored_rosters,
    load_friend_emails,
    normalize_name,
    remove_friend_email,
    resolve_roster_file,
    run_batch_render,
    save_uploaded_roster_overwrite,
    send_email_notification,
    sync_file_to_github,
)

LAST_BATCH_PATH = os.path.join(CURRENT_DIR, ".last_batch.json")
PENDING_REQUESTS_PATH = os.path.join(CURRENT_DIR, ".pending_roster_requests.json")
FREQUENT_LIST_NAMES: list[str] = []
FREQUENT_ORDER_PATH = os.path.join(CURRENT_DIR, ".frequent_list_order.json")


def load_frequent_list(all_roster_names: list[str]) -> list[str]:
    if os.path.exists(FREQUENT_ORDER_PATH):
        try:
            with open(FREQUENT_ORDER_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    cleaned = [normalize_name(x) for x in data if normalize_name(x)]
                    if cleaned:
                        return list(dict.fromkeys(cleaned))
        except Exception:
            pass
    matched = [n for n in FREQUENT_LIST_NAMES if n in all_roster_names]
    if matched:
        return matched
    return all_roster_names[:1] if all_roster_names else []


def save_frequent_list(names: list[str]) -> tuple[bool, str]:
    try:
        cleaned = list(dict.fromkeys(normalize_name(x) for x in names if normalize_name(x)))
        with open(FREQUENT_ORDER_PATH, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)
        gh_token, gh_repo, gh_branch = get_github_sync_config()
        return sync_file_to_github(
            local_file_path=FREQUENT_ORDER_PATH,
            repo_rel_path=os.path.basename(FREQUENT_ORDER_PATH),
            github_token=gh_token,
            github_repo=gh_repo,
            branch=gh_branch,
        )
    except Exception as e:
        return False, f"儲存常用名單失敗：{e}"

MEMBER_MAX_BATCH = 20

st.set_page_config(
    page_title="C.L.F 勤務班表出圖與通知系統 V2",
    page_icon="🗓️",
    layout="wide",
)


def get_secret_or_env(key: str, default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    try:
        if not val and key in st.secrets:
            val = str(st.secrets[key]).strip()
    except Exception:
        pass
    return val or default


def verify_admin_pin(candidate_pin: str) -> bool:
    configured_pin = get_secret_or_env("ADMIN_PIN", "")
    if not configured_pin or not candidate_pin:
        return False
    return hmac.compare_digest(candidate_pin.strip(), configured_pin)


def get_mail_credentials() -> tuple[str, str]:
    sender_email = get_secret_or_env("MAIL_SENDER_EMAIL", DEFAULT_SENDER_EMAIL)
    sender_password = get_secret_or_env("MAIL_SENDER_PASSWORD", DEFAULT_SENDER_PASSWORD)
    return sender_email, sender_password


def get_github_sync_config() -> tuple[str, str, str]:
    token = get_secret_or_env("GITHUB_TOKEN", "")
    repo = get_secret_or_env("GITHUB_REPO", "leooel911/C.L.F_to_image")
    branch = get_secret_or_env("GITHUB_BRANCH", "main")
    return token, repo, branch


def sync_roster_file_to_github() -> tuple[bool, str]:
    gh_token, gh_repo, gh_branch = get_github_sync_config()
    rpath = resolve_roster_file(CURRENT_DIR)
    repo_rel = os.path.basename(rpath)
    return sync_file_to_github(
        local_file_path=rpath,
        repo_rel_path=repo_rel,
        github_token=gh_token,
        github_repo=gh_repo,
        branch=gh_branch,
    )


def load_pending_requests() -> list[dict]:
    if os.path.exists(PENDING_REQUESTS_PATH):
        try:
            with open(PENDING_REQUESTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def save_pending_requests(requests: list[dict]) -> tuple[bool, str]:
    try:
        with open(PENDING_REQUESTS_PATH, "w", encoding="utf-8") as f:
            json.dump(requests, f, ensure_ascii=False, indent=2)
        gh_token, gh_repo, gh_branch = get_github_sync_config()
        return sync_file_to_github(
            local_file_path=PENDING_REQUESTS_PATH,
            repo_rel_path=os.path.basename(PENDING_REQUESTS_PATH),
            github_token=gh_token,
            github_repo=gh_repo,
            branch=gh_branch,
        )
    except Exception as e:
        return False, f"儲存待審核清單失敗：{e}"


def submit_frequent_request(name: str, email_or_acct: str, note: str = "") -> tuple[bool, str]:
    clean_n = normalize_name(name)
    mail_val = normalize_mail_input(email_or_acct, default_domain="@thsrc.com.tw")
    if not clean_n:
        return False, "請填寫人員姓名。"
    if not mail_val or "@" not in mail_val:
        return False, "請填寫有效的信箱或帳號前綴。"

    reqs = load_pending_requests()
    for r in reqs:
        if normalize_name(r.get("name", "")) == clean_n:
            r["email"] = mail_val
            r["note"] = note.strip()
            r["submitted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            save_pending_requests(reqs)
            return True, f"已更新【{clean_n}】的加入常用名單申請，等待管理員審核。"

    reqs.append({
        "name": clean_n,
        "email": mail_val,
        "note": note.strip(),
        "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    save_pending_requests(reqs)
    return True, f"已送出【{clean_n}】加入常用名單的申請，等待管理員審核。"


def save_last_batch(records: list[dict], logs: list[str]):
    try:
        with open(LAST_BATCH_PATH, "w", encoding="utf-8") as f:
            json.dump({"records": records, "logs": logs}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_last_batch() -> tuple[list[dict], list[str]]:
    if os.path.exists(LAST_BATCH_PATH):
        try:
            with open(LAST_BATCH_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("records", []), data.get("logs", [])
        except Exception:
            pass
    return [], []


def parse_target_names(raw_text: str) -> list[str]:
    if not raw_text:
        return []
    parts = re.split(r"[,，、\n\r\t;；]+", raw_text)
    seen = set()
    result = []
    for p in parts:
        cn = normalize_name(p)
        if cn and cn not in seen:
            seen.add(cn)
            result.append(cn)
    return result


def normalize_mail_input(raw_mail: str, default_domain: str = "@thsrc.com.tw") -> str:
    s = (raw_mail or "").strip()
    if not s:
        return ""
    if "@" not in s and default_domain:
        dom = default_domain if default_domain.startswith("@") else f"@{default_domain}"
        return f"{s}{dom}"
    return s


def parse_bulk_roster_lines(bulk_text: str, default_domain: str = "@thsrc.com.tw") -> list[tuple[str, str]]:
    parsed = []
    seen_names = set()
    if not bulk_text:
        return parsed

    paren_matches = re.findall(
        r"([A-Za-z0-9._+-]+(?:@[^,;\s()（）<>]+)?)\s*[\(（]([^\)）]+)[\)）](?:\s*<([^>]+)>)?",
        bulk_text,
    )
    if paren_matches:
        for acct_raw, name_raw, angle_mail in paren_matches:
            acct = acct_raw.strip()
            emp_name = normalize_name(name_raw)
            if not emp_name or emp_name in seen_names:
                continue
            if "_MBOX" in acct.upper() or "北區運轉課" in name_raw:
                continue
            mail_part = (angle_mail or "").strip() or normalize_mail_input(acct, default_domain=default_domain)
            if "@" in mail_part:
                seen_names.add(emp_name)
                parsed.append((emp_name, mail_part))
        if parsed:
            return parsed

    for line in bulk_text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = re.split(r"[,，:\t;；]+", s, maxsplit=1)
        if len(parts) == 2:
            name_part = normalize_name(parts[0])
            mail_part = normalize_mail_input(parts[1], default_domain=default_domain)
            if name_part and mail_part and name_part not in seen_names:
                seen_names.add(name_part)
                parsed.append((name_part, mail_part))
    return parsed


def build_zip_bytes(records: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rec in records:
            img_path = rec.get("image_path", "")
            if img_path and os.path.exists(img_path):
                zf.write(img_path, arcname=os.path.basename(img_path))
    buf.seek(0)
    return buf.getvalue()


@st.dialog("確認寄出班表郵件")
def open_single_mail_confirm_dialog(
    emp_name: str,
    recipient: str,
    file_date_str: str,
    img_path: str,
    current_emails: dict,
    sender_email: str,
    sender_password: str,
):
    st.markdown(f"**收件人員**：{emp_name}")
    st.markdown(f"**收件信箱**：`{recipient or '未設定信箱'}`")
    st.markdown(f"**班表起始日期**：`{file_date_str}`")
    st.markdown(f"**附件圖檔**：`{os.path.basename(img_path)}`")
    if img_path and os.path.exists(img_path):
        st.image(img_path, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("確認寄出", type="primary", use_container_width=True, disabled=not bool(recipient)):
            with st.spinner(f"正在寄送給 {emp_name}..."):
                ok, msg = send_email_notification(
                    file_path=img_path,
                    emp_name=emp_name,
                    file_date_str=file_date_str,
                    friend_emails=current_emails,
                    sender_email=sender_email,
                    sender_password=sender_password,
                )
            st.session_state["last_mail_feedback"] = ("success" if ok else "error", msg)
            st.rerun()
    with c2:
        if st.button("取消", use_container_width=True):
            st.rerun()


@st.dialog("確認批次寄出全部人員班表")
def open_batch_mail_confirm_dialog(
    valid_records: list[dict],
    current_emails: dict,
    sender_email: str,
    sender_password: str,
):
    st.markdown(f"即將寄出共 **{len(valid_records)}** 份班表圖檔，請再次確認收件對象：")
    for r in valid_records:
        nm = r.get("name", "")
        dt = r.get("file_date_str", "")
        rcpt = current_emails.get(normalize_name(nm), "未設定信箱")
        st.caption(f"• **{nm}**（{dt}）→ `{rcpt}`")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("確認全部寄出", type="primary", use_container_width=True):
            sent_ok, sent_fail = [], []
            with st.spinner("正在批次寄送班表信件..."):
                for rec in valid_records:
                    ok, msg = send_email_notification(
                        file_path=rec["image_path"],
                        emp_name=rec["name"],
                        file_date_str=rec["file_date_str"],
                        friend_emails=current_emails,
                        sender_email=sender_email,
                        sender_password=sender_password,
                    )
                    (sent_ok if ok else sent_fail).append(msg)
            st.session_state["batch_mail_results"] = {"ok": sent_ok, "fail": sent_fail}
            st.rerun()
    with c2:
        if st.button("取消", use_container_width=True):
            st.rerun()


def render_single_record_card(
    rec: dict,
    idx: int,
    current_emails: dict,
    sender_email: str,
    sender_password: str,
    is_admin: bool,
):
    emp_name = rec.get("name", "")
    file_date_str = rec.get("file_date_str", "")
    img_path = rec.get("image_path", "")
    status = rec.get("status", "")

    with st.container(border=True):
        st.markdown(f"**{emp_name}**｜起始日期：`{file_date_str}`｜狀態：`{status}`")
        if img_path and os.path.exists(img_path):
            st.image(img_path, use_container_width=True)

            with open(img_path, "rb") as img_f:
                img_bytes = img_f.read()

            if is_admin:
                c_dl, c_mail = st.columns([1, 1])
                with c_dl:
                    st.download_button(
                        label="下載班表圖片",
                        data=img_bytes,
                        file_name=os.path.basename(img_path),
                        mime="image/png",
                        key=f"dl_{idx}_{emp_name}",
                        use_container_width=True,
                    )
                recipient = current_emails.get(normalize_name(emp_name), "")
                with c_mail:
                    if st.button(
                        f"寄送信件給 {emp_name}" + (f"（{recipient}）" if recipient else "（未設定信箱）"),
                        key=f"send_{idx}_{emp_name}",
                        use_container_width=True,
                    ):
                        open_single_mail_confirm_dialog(
                            emp_name=emp_name,
                            recipient=recipient,
                            file_date_str=file_date_str,
                            img_path=img_path,
                            current_emails=current_emails,
                            sender_email=sender_email,
                            sender_password=sender_password,
                        )
            else:
                st.download_button(
                    label="下載班表圖片",
                    data=img_bytes,
                    file_name=os.path.basename(img_path),
                    mime="image/png",
                    key=f"dl_{idx}_{emp_name}",
                    use_container_width=True,
                )
        else:
            st.warning(f"找不到圖檔：{img_path}")


if "batch_records" not in st.session_state or "batch_logs" not in st.session_state:
    init_records, init_logs = load_last_batch()
    st.session_state["batch_records"] = init_records
    st.session_state["batch_logs"] = init_logs

if "upload_dir" not in st.session_state:
    st.session_state["upload_dir"] = tempfile.mkdtemp(prefix="clf_roster_uploads_")

if "selected_targets" not in st.session_state:
    st.session_state["selected_targets"] = ["江立夫"]

if "roster_sync_notice" not in st.session_state:
    st.session_state["roster_sync_notice"] = ""

if "member_req_notice" not in st.session_state:
    st.session_state["member_req_notice"] = ""

if "last_mail_feedback" not in st.session_state:
    st.session_state["last_mail_feedback"] = None

if "batch_mail_results" not in st.session_state:
    st.session_state["batch_mail_results"] = None

if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False

os.makedirs(DEFAULT_RAW_DIR, exist_ok=True)

is_admin = bool(st.session_state.get("is_admin", False))
pending_reqs = load_pending_requests()

st.title("C.L.F 勤務班表出圖與郵件通知系統 V2")
if is_admin:
    st.caption(
        f"目前身分：**管理員模式**｜待審核常用名單申請：{len(pending_reqs)} 筆"
    )
else:
    st.caption("目前身分：**組員自助出圖模式**｜可檢視常用名單、申請加入常用名單、產生與下載班表")

if is_admin and st.session_state.get("last_mail_feedback"):
    fb_type, fb_msg = st.session_state["last_mail_feedback"]
    if fb_type == "success":
        st.success(fb_msg)
    else:
        st.error(fb_msg)
    st.session_state["last_mail_feedback"] = None

if is_admin and st.session_state.get("batch_mail_results"):
    res_obj = st.session_state["batch_mail_results"]
    for m in res_obj.get("ok", []):
        st.success(m)
    for m in res_obj.get("fail", []):
        st.error(m)
    st.session_state["batch_mail_results"] = None

friend_emails = load_friend_emails(CURRENT_DIR)
all_roster_names = list(dict.fromkeys(["江立夫"] + list(friend_emails.keys())))
frequent_list_names = load_frequent_list(all_roster_names)
sheet_employees = extract_all_employees_from_sources(DEFAULT_RAW_DIR, DEFAULT_TXT_PATH)

raw_unit_groups: dict[str, list[str]] = {}
for item in sheet_employees:
    u_code = (item.get("unit") or "TTN").strip().upper()
    p_code = (item.get("pos") or "ALL").strip().upper()
    g_key = f"{u_code} / {p_code}"
    raw_unit_groups.setdefault(g_key, [])
    if item["name"] not in raw_unit_groups[g_key]:
        raw_unit_groups[g_key].append(item["name"])


def _group_sort_key(g_key: str) -> tuple[int, int, str]:
    normalized = g_key.replace(" ", "").upper()
    if "TTN" in normalized:
        unit_rank = 0
    elif "TTC" in normalized:
        unit_rank = 1
    elif "TTS" in normalized:
        unit_rank = 2
    else:
        unit_rank = 9

    if normalized.endswith("/TA") or "/TA" in normalized:
        pos_rank = 0
    elif normalized.endswith("/TM") or "/TM" in normalized:
        pos_rank = 1
    else:
        pos_rank = 5
    return (unit_rank, pos_rank, normalized)


group_map: dict[str, list[str]] = {
    "常用名單": frequent_list_names,
}
for g_key in sorted(raw_unit_groups.keys(), key=_group_sort_key):
    group_map[g_key] = raw_unit_groups[g_key]

all_sheet_names = [item["name"] for item in sheet_employees]
group_map["班表全部人員"] = list(dict.fromkeys(all_roster_names + all_sheet_names))
all_selectable_names = group_map["班表全部人員"]

with st.sidebar:
    st.header("出圖與名單設定")
    st.caption(f"底稿 {len(all_sheet_names)} 人｜名單 {len(all_roster_names)} 人")

    group_options = list(group_map.keys())
    active_group = st.selectbox("快速分類群組", options=group_options, index=0)
    filtered_options = group_map.get(active_group, all_selectable_names)

    batch_limit = FREQUENT_MEMBER_MAX_BATCH if (is_admin or is_frequent_member) else NON_FREQUENT_MAX_BATCH
    ms_key = f"ms_{active_group}"
    allowed_set = set(filtered_options)

    # 僅在該群組 key 尚未建立時初始化，避免每次重跑覆寫使用者按小叉叉的結果
    if ms_key not in st.session_state:
        st.session_state[ms_key] = [n for n in st.session_state["selected_targets"] if n in allowed_set]
    else:
        st.session_state[ms_key] = [n for n in st.session_state[ms_key] if n in allowed_set]

    def _sync_from_multiselect():
        picked = st.session_state.get(ms_key, [])
        others = [
            n for n in st.session_state["selected_targets"]
            if n not in allowed_set and n in all_selectable_names
        ]
        merged = list(dict.fromkeys(others + picked))
        if not is_admin and len(merged) > batch_limit:
            merged = merged[:batch_limit]
            st.session_state[ms_key] = [n for n in merged if n in allowed_set]
        st.session_state["selected_targets"] = merged

    selected_friends = st.multiselect(
        f"勾選／搜尋人員（{active_group}・{len(filtered_options)} 人）",
        options=filtered_options,
        key=ms_key,
        on_change=_sync_from_multiselect,
    )

    other_selected = [
        n for n in st.session_state["selected_targets"]
        if n not in allowed_set and n in all_selectable_names
    ]
    combined_sel = list(dict.fromkeys(other_selected + selected_friends))
    if not is_admin and len(combined_sel) > batch_limit:
        combined_sel = combined_sel[:batch_limit]
    st.session_state["selected_targets"] = combined_sel

    if st.session_state["selected_targets"]:
        if st.button("清空已選", use_container_width=True):
            st.session_state["selected_targets"] = []
            for k in list(st.session_state.keys()):
                if str(k).startswith("ms_"):
                    st.session_state[k] = []
            st.rerun()

    if st.session_state["selected_targets"]:
        st.caption(
            f"已選 {len(st.session_state['selected_targets'])} 人："
            f"{'、'.join(st.session_state['selected_targets'][:6])}"
            f"{' 等' if len(st.session_state['selected_targets']) > 6 else ''}"
        )

    extra_names_input = st.text_area(
        "其他指定姓名（逗號或換行分隔）",
        value="",
        height=65,
        placeholder="例如：王小明, 陳小華",
    )

    with st.expander(f"檢視 {active_group} 名單明細（{len(filtered_options)} 人）", expanded=False):
        if active_group == "常用名單":
            for person in filtered_options[:60]:
                mail_addr = friend_emails.get(person, "未設定信箱")
                st.caption(f"• {person} — `{mail_addr}`")
            if len(filtered_options) > 60:
                st.caption(f"...其餘 {len(filtered_options) - 60} 人請在上方下拉框直接搜尋姓名。")
        else:
            st.caption("、".join(filtered_options[:80]) + (" ..." if len(filtered_options) > 80 else ""))

    if not is_admin:
        st.divider()
        st.subheader("申請加入常用名單")
        if st.session_state.get("member_req_notice"):
            st.caption(st.session_state["member_req_notice"])
        req_name = st.text_input("申請姓名", key="member_req_name", placeholder="例如：王小明")
        req_mail = st.text_input("信箱或帳號前綴", key="member_req_mail", placeholder="例如：ming_wang 或 ming_wang@thsrc.com.tw")
        req_note = st.text_input("備註／組別（選填）", key="member_req_note", placeholder="例如：TTN / TA")
        if st.button("申請加入常用名單", use_container_width=True):
            ok_req, msg_req = submit_frequent_request(req_name, req_mail, req_note)
            if ok_req:
                st.session_state["member_req_notice"] = msg_req
                st.success(msg_req)
            else:
                st.error(msg_req)

    if is_admin:
        skip_unchanged = st.checkbox("略過未變動且已存在的班表圖", value=False)
        force_regenerate = st.checkbox("強制重新繪製（忽略快取）", value=True)
        sync_github_on_upload = st.checkbox("上傳覆蓋時同步更新 GitHub 底稿", value=True)

        st.divider()
        st.subheader(f"待審核常用名單申請（{len(pending_reqs)} 筆）")
        if pending_reqs:
            for idx_r, r_item in enumerate(list(pending_reqs)):
                r_name = r_item.get("name", "")
                r_mail = r_item.get("email", "")
                r_note = r_item.get("note", "")
                r_time = r_item.get("submitted_at", "")
                with st.container(border=True):
                    st.markdown(f"**{r_name}** — `{r_mail}`")
                    st.caption(f"{r_time}" + (f"｜{r_note}" if r_note else ""))
                    ap_c1, ap_c2 = st.columns(2)
                    with ap_c1:
                        if st.button("核准加入", key=f"approve_req_{idx_r}_{r_name}", use_container_width=True):
                            ok_add, msg_add = append_or_update_friend_email(r_name, r_mail, CURRENT_DIR)
                            if ok_add:
                                _, gh_msg = sync_roster_file_to_github()
                                next_freq = list(dict.fromkeys(frequent_list_names + [r_name]))
                                _, gh_freq_msg = save_frequent_list(next_freq)
                                updated_reqs = [x for i, x in enumerate(pending_reqs) if i != idx_r]
                                save_pending_requests(updated_reqs)
                                st.session_state["roster_sync_notice"] = (
                                    f"已核准【{r_name}】加入常用名單與發送名單（{gh_msg}｜{gh_freq_msg}）"
                                )
                                st.rerun()
                            else:
                                st.error(msg_add)
                    with ap_c2:
                        if st.button("駁回", key=f"reject_req_{idx_r}_{r_name}", use_container_width=True):
                            updated_reqs = [x for i, x in enumerate(pending_reqs) if i != idx_r]
                            save_pending_requests(updated_reqs)
                            st.rerun()
        else:
            st.caption("目前沒有待審核的加入申請。")

        st.divider()
        st.subheader("常用名單選單管理")
        freq_menu_mode = st.radio(
            "常用名單操作",
            options=["挑選現有常用名單修改", "從既有／班表人員加入常用名單"],
            horizontal=True,
            key="freq_menu_mode",
        )

        if freq_menu_mode == "挑選現有常用名單修改":
            if frequent_list_names:
                picked_freq_name = st.selectbox(
                    "選擇常用名單人員",
                    options=frequent_list_names,
                    key="freq_pick_existing_select",
                )
                picked_freq_mail = st.text_input(
                    "常用名單信箱",
                    value=friend_emails.get(picked_freq_name, ""),
                    key=f"freq_pick_mail_{picked_freq_name}",
                )
                fm_c1, fm_c2 = st.columns(2)
                with fm_c1:
                    if st.button("儲存信箱變更", key="btn_save_freq_mail", use_container_width=True):
                        if picked_freq_mail.strip():
                            ok, msg = append_or_update_friend_email(picked_freq_name, picked_freq_mail.strip(), CURRENT_DIR)
                            if ok:
                                _, gh_msg = sync_roster_file_to_github()
                                st.session_state["roster_sync_notice"] = f"已更新【{picked_freq_name}】信箱（{gh_msg}）"
                                st.rerun()
                            else:
                                st.error(msg)
                        else:
                            st.warning("請輸入信箱後再儲存。")
                with fm_c2:
                    if st.button("移出常用名單", key="btn_remove_from_freq", use_container_width=True):
                        next_freq = [n for n in frequent_list_names if n != picked_freq_name]
                        _, gh_f_msg = save_frequent_list(next_freq)
                        st.session_state["roster_sync_notice"] = f"已將【{picked_freq_name}】移出常用名單（{gh_f_msg}）。"
                        st.rerun()
            else:
                st.caption("目前常用名單為空。")
        else:
            candidate_pool = [n for n in all_selectable_names if n not in frequent_list_names]
            if candidate_pool:
                picked_candidate = st.selectbox(
                    "選擇要加入常用名單的人員",
                    options=candidate_pool,
                    key="freq_add_candidate_select",
                )
                candidate_mail = st.text_input(
                    "確認或補填信箱（可留空沿用已存信箱）",
                    value=friend_emails.get(picked_candidate, ""),
                    key=f"freq_add_candidate_mail_{picked_candidate}",
                )
                if st.button("加入常用名單", key="btn_add_to_freq_menu", use_container_width=True):
                    next_freq = list(dict.fromkeys(frequent_list_names + [picked_candidate]))
                    _, gh_f_msg = save_frequent_list(next_freq)
                    if candidate_mail.strip():
                        ok_m, msg_m = append_or_update_friend_email(picked_candidate, candidate_mail.strip(), CURRENT_DIR)
                        if ok_m:
                            _, gh_msg = sync_roster_file_to_github()
                            st.session_state["roster_sync_notice"] = f"已將【{picked_candidate}】加入常用名單並同步（{gh_msg}）"
                        else:
                            st.error(msg_m)
                    else:
                        st.session_state["roster_sync_notice"] = f"已將【{picked_candidate}】加入常用名單（{gh_f_msg}）。"
                    st.rerun()
            else:
                st.caption("可選人員皆已在常用名單中，或可在下方新增人員。")

        st.divider()
        st.subheader("編輯／新增發送名單")
        if st.session_state.get("roster_sync_notice"):
            st.caption(st.session_state["roster_sync_notice"])

        roster_mode = st.radio(
            "操作模式",
            options=["編輯／刪除現有名單", "快速新增／批次貼上"],
            horizontal=True,
        )

        if roster_mode == "編輯／刪除現有名單":
            existing_names = list(friend_emails.keys())
            if existing_names:
                target_edit_name = st.selectbox("選擇人員", options=existing_names, key="roster_edit_select")
                edit_email_val = st.text_input(
                    "電子信箱",
                    value=friend_emails.get(target_edit_name, ""),
                    key=f"roster_edit_email_{target_edit_name}",
                )
                ed_c1, ed_c2 = st.columns(2)
                with ed_c1:
                    if st.button("更新信箱", use_container_width=True):
                        ok, msg = append_or_update_friend_email(target_edit_name, edit_email_val, CURRENT_DIR)
                        if ok:
                            _, gh_msg = sync_roster_file_to_github()
                            st.session_state["roster_sync_notice"] = f"{msg}（{gh_msg}）"
                            st.rerun()
                        else:
                            st.error(msg)
                with ed_c2:
                    if st.button("刪除人員", use_container_width=True):
                        ok, msg = remove_friend_email(target_edit_name, CURRENT_DIR)
                        if ok:
                            _, gh_msg = sync_roster_file_to_github()
                            st.session_state["roster_sync_notice"] = f"{msg}（{gh_msg}）"
                            st.rerun()
                        else:
                            st.error(msg)
            else:
                st.caption("目前發送名單為空。")
        else:
            default_domain = st.text_input("預設信箱網域（未含 @ 時自動補上）", value="@thsrc.com.tw", key="roster_default_domain")
            new_name = st.text_input("單筆姓名", key="roster_new_name", placeholder="例如：王小明")
            new_email = st.text_input("單筆信箱或帳號前綴", key="roster_new_email", placeholder="例如：ming_wang 或 ming_wang@thsrc.com.tw")
            if st.button("快速新增單筆", use_container_width=True):
                final_mail = normalize_mail_input(new_email, default_domain=default_domain)
                ok, msg = append_or_update_friend_email(new_name, final_mail, CURRENT_DIR)
                if ok:
                    _, gh_msg = sync_roster_file_to_github()
                    st.session_state["roster_sync_notice"] = f"{msg}（{gh_msg}）"
                    st.rerun()
                else:
                    st.error(msg)

            bulk_text = st.text_area(
                "批次貼上（每行一筆：姓名, 信箱或帳號）",
                height=90,
                key="roster_bulk_text",
                placeholder="王小明, ming_wang\n陳小華: hua_chen@thsrc.com.tw",
            )
            if st.button("批次匯入名單", use_container_width=True):
                items = parse_bulk_roster_lines(bulk_text, default_domain=default_domain)
                if not items:
                    st.error("請依「姓名, 信箱」格式輸入至少一行。")
                else:
                    ok_cnt, fail_msgs = 0, []
                    for p_name, p_mail in items:
                        ok, msg = append_or_update_friend_email(p_name, p_mail, CURRENT_DIR)
                        if ok:
                            ok_cnt += 1
                        else:
                            fail_msgs.append(msg)
                    if ok_cnt:
                        _, gh_msg = sync_roster_file_to_github()
                        st.session_state["roster_sync_notice"] = f"已批次儲存 {ok_cnt} 筆名單（{gh_msg}）。"
                    for fm_err in fail_msgs:
                        st.error(fm_err)
                    if ok_cnt:
                        st.rerun()

        with st.expander("目前發送名單一覽"):
            for person, mail in list(load_friend_emails(CURRENT_DIR).items())[:60]:
                st.write(f"- **{person}**：`{mail}`")
    else:
        skip_unchanged = False
        force_regenerate = True
        sync_github_on_upload = False

    st.divider()
    with st.expander("管理員模式切換", expanded=False):
        if is_admin:
            st.success("已解鎖管理員模式")
            if st.button("鎖定回組員模式", use_container_width=True):
                st.session_state["is_admin"] = False
                st.rerun()
        else:
            pin_input = st.text_input("輸入管理員 PIN 碼", type="password", key="admin_pin_input")
            if st.button("解鎖管理員模式", use_container_width=True):
                if verify_admin_pin(pin_input):
                    st.session_state["is_admin"] = True
                    st.rerun()
                else:
                    st.error("PIN 碼錯誤或尚未於 Secrets 設定 ADMIN_PIN。")

if is_admin:
    st.subheader("1. 後台班表底稿管理與上傳覆蓋")
    stored_rosters = list_stored_rosters(DEFAULT_RAW_DIR)
    if stored_rosters:
        st.markdown("**後台已留存底稿（依同單位同職位自動覆蓋，出圖時免重傳）：**")
        for item in stored_rosters:
            st.caption(f"• `{item['filename']}`｜單位/職位：`{item['unit']}/{item['pos']}`｜更新時間：`{item['updated_at']}`")
    else:
        st.info("後台 `班表原檔` 目前尚無留存底稿。上傳班表檔案後，系統會依同單位同職位自動留存並覆蓋舊版。")

    uploaded_files = st.file_uploader(
        "上傳新的班表原檔（將自動覆蓋同單位同職位後台底稿，支援 .xls, .xlsx, .csv，可多選）",
        type=["xls", "xlsx", "csv"],
        accept_multiple_files=True,
    )

    gh_token, gh_repo, gh_branch = get_github_sync_config()

    if uploaded_files:
        upload_dir = st.session_state["upload_dir"]
        os.makedirs(upload_dir, exist_ok=True)
        for uf in uploaded_files:
            safe_name = os.path.basename(uf.name)
            tmp_path = os.path.join(upload_dir, safe_name)
            with open(tmp_path, "wb") as f:
                f.write(uf.getbuffer())

            dest_path, u_code, p_code, replaced = save_uploaded_roster_overwrite(
                src_file_path=tmp_path,
                original_filename=safe_name,
                raw_dir=DEFAULT_RAW_DIR,
            )
            action_txt = "已覆蓋同單位同職位舊底稿" if replaced else "已新增至後台底稿"
            st.success(f"`{safe_name}` → `{os.path.basename(dest_path)}`（{u_code}/{p_code}，{action_txt}）")

            if sync_github_on_upload:
                repo_rel = f"班表原檔/{os.path.basename(dest_path)}"
                ok_gh, msg_gh = sync_file_to_github(
                    local_file_path=dest_path,
                    repo_rel_path=repo_rel,
                    github_token=gh_token,
                    github_repo=gh_repo,
                    branch=gh_branch,
                )
                if ok_gh:
                    st.caption(f"☁️ {msg_gh}")
                else:
                    st.caption(f"ℹ️ {msg_gh}")

col_run1, col_run2 = st.columns([1, 2])
with col_run1:
    run_clicked = st.button("執行批次出圖", type="primary", use_container_width=True)

target_names = list(dict.fromkeys(st.session_state["selected_targets"] + parse_target_names(extra_names_input)))
if not is_admin and len(target_names) > MEMBER_MAX_BATCH:
    target_names = target_names[:MEMBER_MAX_BATCH]

if run_clicked:
    if not target_names:
        st.warning("請先至少選擇或輸入一位人員姓名。")
    else:
        with st.spinner("正在讀取後台底稿並繪製圖檔..."):
            records, logs = run_batch_render(
                target_names=target_names,
                raw_dir=DEFAULT_RAW_DIR,
                out_dir=DEFAULT_OUT_DIR,
                txt_path=DEFAULT_TXT_PATH,
                skip_unchanged=skip_unchanged,
                force_regenerate=force_regenerate,
            )
        st.session_state["batch_records"] = records
        st.session_state["batch_logs"] = logs
        save_last_batch(records, logs)

if is_admin:
    with st.expander("執行紀錄（Logs）", expanded=False):
        for line in st.session_state.get("batch_logs", []):
            st.text(line)

st.divider()
st.subheader("2. 班表圖檔預覽與下載" + ("／寄送" if is_admin else ""))

records = st.session_state.get("batch_records", [])
current_emails = load_friend_emails(CURRENT_DIR)
sender_email, sender_password = get_mail_credentials()

if not records:
    st.info("尚未有產出的班表圖檔。請在左側選擇人員或群組後點選「執行批次出圖」。")
else:
    valid_records = [r for r in records if r.get("image_path") and os.path.exists(r.get("image_path", ""))]

    if is_admin:
        top_c1, top_c2, top_c3 = st.columns([1.2, 1.3, 1.5])
    else:
        top_c1, top_c2 = st.columns([1.4, 1.6])

    with top_c1:
        view_mode = st.radio(
            "顯示方式",
            options=["分頁切換（推薦多人）", "單人下拉檢視", "全部展開"],
            index=0 if len(valid_records) > 1 else 2,
            horizontal=False,
        )
    with top_c2:
        if len(valid_records) >= 2:
            st.download_button(
                label=f"一鍵下載全部圖檔（ZIP・{len(valid_records)}張）",
                data=build_zip_bytes(valid_records),
                file_name="TTN班表_批次圖檔.zip",
                mime="application/zip",
                use_container_width=True,
            )
    if is_admin:
        with top_c3:
            if st.button("批次寄出全部人員班表", use_container_width=True):
                open_batch_mail_confirm_dialog(
                    valid_records=valid_records,
                    current_emails=current_emails,
                    sender_email=sender_email,
                    sender_password=sender_password,
                )

    if view_mode == "分頁切換（推薦多人）" and len(valid_records) > 1:
        tab_labels = [f"{r.get('name', '')} ({r.get('file_date_str', '')})" for r in records]
        tabs = st.tabs(tab_labels)
        for idx, (tab, rec) in enumerate(zip(tabs, records)):
            with tab:
                render_single_record_card(rec, idx, current_emails, sender_email, sender_password, is_admin)

    elif view_mode == "單人下拉檢視" and len(records) > 0:
        options_map = {f"{i}_{r.get('name', '')} ({r.get('file_date_str', '')})": (i, r) for i, r in enumerate(records)}
        chosen_key = st.selectbox("選擇要檢視的人員班表", options=list(options_map.keys()))
        chosen_idx, chosen_rec = options_map[chosen_key]
        render_single_record_card(chosen_rec, chosen_idx, current_emails, sender_email, sender_password, is_admin)

    else:
        for idx, rec in enumerate(records):
            render_single_record_card(rec, idx, current_emails, sender_email, sender_password, is_admin)
