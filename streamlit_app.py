#!/usr/bin/env python3
"""
C.L.F 勤務班表出圖與郵件通知系統（雲端／手機操作版 V2）
"""

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
    run_batch_render,
    save_uploaded_roster_overwrite,
    send_email_notification,
    sync_file_to_github,
)

LAST_BATCH_PATH = os.path.join(CURRENT_DIR, ".last_batch.json")

st.set_page_config(
    page_title="C.L.F 勤務班表出圖與通知系統 V2",
    page_icon="🗓️",
    layout="wide",
)


def get_mail_credentials() -> tuple[str, str]:
    sender_email = os.environ.get("MAIL_SENDER_EMAIL", "").strip()
    sender_password = os.environ.get("MAIL_SENDER_PASSWORD", "").strip()
    try:
        if not sender_email and "MAIL_SENDER_EMAIL" in st.secrets:
            sender_email = str(st.secrets["MAIL_SENDER_EMAIL"]).strip()
        if not sender_password and "MAIL_SENDER_PASSWORD" in st.secrets:
            sender_password = str(st.secrets["MAIL_SENDER_PASSWORD"]).strip()
    except Exception:
        pass
    return (
        sender_email or DEFAULT_SENDER_EMAIL,
        sender_password or DEFAULT_SENDER_PASSWORD,
    )


def get_github_sync_config() -> tuple[str, str, str]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPO", "leooel911/C.L.F_to_image").strip()
    branch = os.environ.get("GITHUB_BRANCH", "main").strip()
    try:
        if not token and "GITHUB_TOKEN" in st.secrets:
            token = str(st.secrets["GITHUB_TOKEN"]).strip()
        if "GITHUB_REPO" in st.secrets:
            repo = str(st.secrets["GITHUB_REPO"]).strip()
        if "GITHUB_BRANCH" in st.secrets:
            branch = str(st.secrets["GITHUB_BRANCH"]).strip()
    except Exception:
        pass
    return token, repo, branch


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


def build_zip_bytes(records: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rec in records:
            img_path = rec.get("image_path", "")
            if img_path and os.path.exists(img_path):
                zf.write(img_path, arcname=os.path.basename(img_path))
    buf.seek(0)
    return buf.getvalue()


def render_single_record_card(rec: dict, idx: int, current_emails: dict, sender_email: str, sender_password: str):
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
                    with st.spinner(f"正在寄送給 {emp_name}..."):
                        ok, msg = send_email_notification(
                            file_path=img_path,
                            emp_name=emp_name,
                            file_date_str=file_date_str,
                            friend_emails=current_emails,
                            sender_email=sender_email,
                            sender_password=sender_password,
                        )
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
        else:
            st.warning(f"找不到圖檔：{img_path}")


if "batch_records" not in st.session_state or "batch_logs" not in st.session_state:
    init_records, init_logs = load_last_batch()
    st.session_state["batch_records"] = init_records
    st.session_state["batch_logs"] = init_logs

if "upload_dir" not in st.session_state:
    st.session_state["upload_dir"] = tempfile.mkdtemp(prefix="clf_roster_uploads_")

os.makedirs(DEFAULT_RAW_DIR, exist_ok=True)

st.title("C.L.F 勤務班表出圖與郵件通知系統 V2")
st.caption("支援後台留存底稿（同單位同職位覆蓋）、班表全員自動帶入選單、分頁檢視與 ZIP 批次下載")

friend_emails = load_friend_emails(CURRENT_DIR)
sheet_employees = extract_all_employees_from_sources(DEFAULT_RAW_DIR, DEFAULT_TXT_PATH)
sheet_names = [item["name"] for item in sheet_employees]
all_selectable_names = list(dict.fromkeys(["江立夫"] + list(friend_emails.keys()) + sheet_names))

with st.sidebar:
    st.header("出圖與名單設定")
    st.caption(f"已從班表底稿自動帶出 {len(sheet_names)} 位人員")
    selected_friends = st.multiselect(
        "快速選擇名單人員（含班表全員）",
        options=all_selectable_names,
        default=[n for n in ["江立夫"] if n in all_selectable_names],
    )
    extra_names_input = st.text_area(
        "其他指定姓名（可用逗號或換行分隔）",
        value="",
        height=70,
        placeholder="例如：王小明, 陳小華",
    )
    skip_unchanged = st.checkbox("略過未變動且已存在的班表圖", value=False)
    force_regenerate = st.checkbox("強制重新繪製（忽略快取）", value=True)
    sync_github_on_upload = st.checkbox("上傳覆蓋時同步更新 GitHub 底稿", value=True)

    st.divider()
    st.subheader("新增／更新發送名單")
    new_name = st.text_input("人員姓名", key="roster_new_name")
    new_email = st.text_input("電子信箱", key="roster_new_email")
    if st.button("儲存至發送名單", use_container_width=True):
        ok, msg = append_or_update_friend_email(new_name, new_email, CURRENT_DIR)
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)

    with st.expander("目前發送名單一覽"):
        for person, mail in load_friend_emails(CURRENT_DIR).items():
            st.write(f"- **{person}**：`{mail}`")

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

target_names = list(dict.fromkeys(selected_friends + parse_target_names(extra_names_input)))

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

with st.expander("執行紀錄（Logs）", expanded=False):
    for line in st.session_state.get("batch_logs", []):
        st.text(line)

st.divider()
st.subheader("2. 班表圖檔預覽、下載與寄送")

records = st.session_state.get("batch_records", [])
current_emails = load_friend_emails(CURRENT_DIR)
sender_email, sender_password = get_mail_credentials()

if not records:
    st.info("尚未有產出的班表圖檔。請確認後台底稿或上傳班表後點選「執行批次出圖」。")
else:
    valid_records = [r for r in records if r.get("image_path") and os.path.exists(r.get("image_path", ""))]

    top_c1, top_c2, top_c3 = st.columns([1.2, 1.3, 1.5])
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
    with top_c3:
        if st.button("批次寄出全部人員班表", use_container_width=True):
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
            for m in sent_ok:
                st.success(m)
            for m in sent_fail:
                st.error(m)

    if view_mode == "分頁切換（推薦多人）" and len(valid_records) > 1:
        tab_labels = [f"{r.get('name', '')} ({r.get('file_date_str', '')})" for r in records]
        tabs = st.tabs(tab_labels)
        for idx, (tab, rec) in enumerate(zip(tabs, records)):
            with tab:
                render_single_record_card(rec, idx, current_emails, sender_email, sender_password)

    elif view_mode == "單人下拉檢視" and len(records) > 0:
        options_map = {f"{i}_{r.get('name', '')} ({r.get('file_date_str', '')})": (i, r) for i, r in enumerate(records)}
        chosen_key = st.selectbox("選擇要檢視的人員班表", options=list(options_map.keys()))
        chosen_idx, chosen_rec = options_map[chosen_key]
        render_single_record_card(chosen_rec, chosen_idx, current_emails, sender_email, sender_password)

    else:
        for idx, rec in enumerate(records):
            render_single_record_card(rec, idx, current_emails, sender_email, sender_password)
