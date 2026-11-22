#!/usr/bin/env python3
"""
C.L.F 勤務班表出圖與郵件通知系統（雲端／手機操作版 V2）
"""

import json
import os
import re
import tempfile

import streamlit as st

from schedule_plotter import (
    CURRENT_DIR,
    DEFAULT_OUT_DIR,
    DEFAULT_RAW_DIR,
    DEFAULT_SENDER_EMAIL,
    DEFAULT_SENDER_PASSWORD,
    DEFAULT_TXT_PATH,
    append_or_update_friend_email,
    load_friend_emails,
    normalize_name,
    run_batch_render,
    send_email_notification,
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


if "batch_records" not in st.session_state or "batch_logs" not in st.session_state:
    init_records, init_logs = load_last_batch()
    st.session_state["batch_records"] = init_records
    st.session_state["batch_logs"] = init_logs

if "upload_dir" not in st.session_state:
    st.session_state["upload_dir"] = tempfile.mkdtemp(prefix="clf_roster_uploads_")

st.title("C.L.F 勤務班表出圖與郵件通知系統 V2")
st.caption("支援手機上傳班表原檔、批次出圖、圖片下載與郵件通知")

friend_emails = load_friend_emails(CURRENT_DIR)
default_targets = ["江立夫"] + [k for k in friend_emails.keys() if k != "江立夫"]

with st.sidebar:
    st.header("出圖與名單設定")
    selected_friends = st.multiselect(
        "快速選擇名單人員",
        options=default_targets,
        default=["江立夫"],
    )
    extra_names_input = st.text_area(
        "其他指定姓名（可用逗號或換行分隔）",
        value="",
        height=70,
        placeholder="例如：王小明, 陳小華",
    )
    skip_unchanged = st.checkbox("略過未變動且已存在的班表圖", value=False)
    force_regenerate = st.checkbox("強制重新繪製（忽略快取）", value=True)

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

st.subheader("1. 上傳班表原檔（手機／雲端必用）")
uploaded_files = st.file_uploader(
    "選擇班表檔案（支援 .xls, .xlsx, .csv，可多選）",
    type=["xls", "xlsx", "csv"],
    accept_multiple_files=True,
)

active_raw_dir = DEFAULT_RAW_DIR
if uploaded_files:
    upload_dir = st.session_state["upload_dir"]
    os.makedirs(upload_dir, exist_ok=True)
    for uf in uploaded_files:
        safe_name = os.path.basename(uf.name)
        save_path = os.path.join(upload_dir, safe_name)
        with open(save_path, "wb") as f:
            f.write(uf.getbuffer())
    active_raw_dir = upload_dir
    st.info(f"已載入 {len(uploaded_files)} 個上傳檔案，將以此批檔案優先出圖。")

col_run1, col_run2 = st.columns([1, 2])
with col_run1:
    run_clicked = st.button("執行批次出圖", type="primary", use_container_width=True)

target_names = list(dict.fromkeys(selected_friends + parse_target_names(extra_names_input)))

if run_clicked:
    if not target_names:
        st.warning("請先至少選擇或輸入一位人員姓名。")
    else:
        with st.spinner("正在解析班表並繪製圖檔..."):
            records, logs = run_batch_render(
                target_names=target_names,
                raw_dir=active_raw_dir,
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
    st.info("尚未有產出的班表圖檔。請上傳班表或點選「執行批次出圖」。")
else:
    for idx, rec in enumerate(records):
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
