# === 必要なモジュール読み込み ===
import streamlit as st
import pandas as pd
import random
import re
import gspread
from google.oauth2.service_account import Credentials

# === Google Sheets 設定 ===
IMAGE_SHEET_ID = "1KUxQDhhnYS6tj4pFYAHwq9SzWxx3iDotTGXSzFUTU-s"
LOG_SHEET_ID = "1yQuifGNG8e77ka5HlJariXxgqPffrIviDZKgmS9FGCg"
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
gc = gspread.authorize(credentials)
image_sheet = gc.open_by_key(IMAGE_SHEET_ID)
log_sheet = gc.open_by_key(LOG_SHEET_ID)

required_cols = ["回答者", "親フォルダ", "時間", "選択フォルダ", "画像ファイル名", "①未融合", "②接触", "③融合中", "④完全融合"]
skip_cols = ["回答者", "親フォルダ", "時間", "選択フォルダ", "画像ファイル名", "スキップ理由"]

@st.cache_data(ttl=60)
def load_ws_data(sheet_id: str, ws_name: str, header_cols: list) -> pd.DataFrame:
    gc = gspread.authorize(credentials)
    sheet = gc.open_by_key(sheet_id)
    try:
        ws = sheet.worksheet(ws_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sheet.add_worksheet(title=ws_name, rows="1000", cols=str(len(header_cols)))
        ws.append_row(header_cols)
        return pd.DataFrame(columns=header_cols)
    records = ws.get_all_records()
    if not records:
        ws.clear()
        ws.append_row(header_cols)
        return pd.DataFrame(columns=header_cols)
    return pd.DataFrame(records)

def df_to_sheet_to(sheet_obj, df, ws_name):
    ws = sheet_obj.worksheet(ws_name)
    ws.clear()
    if not df.empty:
        ws.update([df.columns.tolist()] + df.values.tolist())

def flush_buffer_to_sheet():
    if "buffered_entries" in st.session_state and st.session_state.buffered_entries:
        buffered_df = pd.DataFrame(st.session_state.buffered_entries)

        # 保存先の既存データを取得（更新用）
        existing_df = st.session_state.existing_df

        # 古いバッファによる上書きを防ぐため、バッファの内容で重複を上書き（新しい方を優先）
        combined_df = pd.concat([buffered_df, existing_df], ignore_index=True)
        combined_df.drop_duplicates(subset=["回答者", "選択フォルダ", "画像ファイル名"], keep="first", inplace=True)

        # 分類件数集計
        summary = combined_df.groupby(["選択フォルダ", "時間"])[["①未融合", "②接触", "③融合中", "④完全融合"]].sum().reset_index()
        summary.insert(0, "一意ID", summary["選択フォルダ"] + "_" + summary["時間"])

        # シートに保存
        df_to_sheet_to(log_sheet, combined_df, "今回の評価")
        df_to_sheet_to(log_sheet, summary, "分類別件数")
        df_to_sheet_to(log_sheet, st.session_state.skip_df, "スキップログ")

        # セッション更新
        st.session_state.existing_df = combined_df
        st.session_state.buffered_entries = []

        st.sidebar.success("保存しました")


USER_CREDENTIALS = {"mamiya": "a", "arai": "a", "yamazaki": "protoplast"}

st.set_page_config(page_title="融合度評価", layout="centered")
st.title("融合度評価 - フラッシュカード")
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.subheader("ログイン")
    input_username = st.text_input("ユーザー名")
    input_password = st.text_input("パスワード", type="password")
    if st.button("ログイン"):
        if input_username in USER_CREDENTIALS and USER_CREDENTIALS[input_username] == input_password:
            st.session_state.authenticated = True
            st.session_state.username = re.sub(r'[^a-zA-Z0-9_一-龯ぁ-んァ-ヶ]', '_', input_username.strip())
            st.success("ログイン成功")
            st.rerun()
        else:
            st.error("ユーザー名またはパスワードが違います")
    st.stop()

username = st.session_state.username
st.sidebar.markdown(f"**ログイン中:** `{username}`")
combined_df = load_ws_data(LOG_SHEET_ID, "今回の評価", required_cols)
st.session_state.existing_df = combined_df.copy()
st.session_state.skip_df = load_ws_data(LOG_SHEET_ID, "スキップログ", skip_cols)
image_list_df = load_ws_data(IMAGE_SHEET_ID, "画像リスト", ["フォルダ", "画像ファイル名", "画像URL"])

if "folder_order" not in st.session_state:
    all_folders = image_list_df["フォルダ"].unique().tolist()
    random.shuffle(all_folders)
    st.session_state.folder_order = all_folders
    st.session_state.folder_index = 0

folder_names = st.session_state.folder_order
if st.session_state.folder_index >= len(folder_names):
    st.success("すべてのフォルダを評価しました！")
    st.stop()

selected_folder = folder_names[st.session_state.folder_index]
folder_images = image_list_df[image_list_df["フォルダ"] == selected_folder]

user_df = combined_df[combined_df["回答者"] == username].copy()
answered_pairs = set(zip(user_df["選択フォルダ"], user_df["画像ファイル名"]))
skip_df = st.session_state.skip_df
skipped_pairs = set(zip(skip_df["選択フォルダ"], skip_df["画像ファイル名"]))
done_pairs = answered_pairs.union(skipped_pairs)

folder_images["pair"] = list(zip(folder_images["フォルダ"], folder_images["画像ファイル名"]))
filtered_images = folder_images[~folder_images["pair"].isin(done_pairs)].drop(columns=["pair"])

if filtered_images.empty:
    st.session_state.folder_index += 1
    st.rerun()

if "image_files" not in st.session_state:
    st.session_state.image_files = filtered_images.reset_index(drop=True)
    st.session_state.index = 0

if st.session_state.index >= len(st.session_state.image_files):
    flush_buffer_to_sheet()
    st.session_state.folder_index += 1
    st.session_state.pop("image_files", None)
    st.session_state.pop("index", None)
    st.rerun()

row = st.session_state.image_files.iloc[st.session_state.index]
current_file = row["画像ファイル名"]
current_url = row["画像URL"]

st.progress((st.session_state.index + 1) / len(st.session_state.image_files))
st.image(current_url, use_container_width=True)

col1, col2, col3, col4 = st.columns(4)
with col1:
    val_1 = st.number_input("\u2460未融合", min_value=0, max_value=1000, step=1,key=f"val1_{current_file}")
with col2:
    val_2 = st.number_input("\u2461接触", min_value=0, max_value=1000, step=1,key=f"val2_{current_file}")
with col3:
    val_3 = st.number_input("\u2462融合中", min_value=0, max_value=1000, step=1,key=f"val3_{current_file}")
with col4:
    val_4 = st.number_input("\u2463完全融合", min_value=0, max_value=1000, step=1,key=f"val4_{current_file}")

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("← 戻る"):
        if st.session_state.index > 0:
            st.session_state.index -= 1
            st.rerun()

with col2:
    if st.button("スキップ"):
        folder_for_this_image = row["フォルダ"]
        time_match = re.search(r'(\d+min)', folder_for_this_image)
        time_str = time_match.group(1) if time_match else "不明"

        skip_entry = {
            "回答者": username,
            "親フォルダ": "mix",
            "時間": time_str,
            "選択フォルダ": folder_for_this_image,
            "画像ファイル名": current_file,
            "スキップ理由": "判別不能"
        }

        st.session_state.skip_df = pd.concat([st.session_state.skip_df, pd.DataFrame([skip_entry])], ignore_index=True)
        st.session_state.index += 1
        st.rerun()

with col3:
    if st.button("進む →"):
        if val_1 + val_2 + val_3 + val_4 == 0:
            st.warning("少なくとも1つは分類してください")
        else:
            folder_for_this_image = row["フォルダ"]  # image_list_dfから来てる元の情報を使う
            time_match = re.search(r'(\d+min)', folder_for_this_image)
            time_str = time_match.group(1) if time_match else "不明"

            new_entry = {
                "回答者": username,
                "親フォルダ": "mix",
                "時間": time_str,
                "選択フォルダ": folder_for_this_image,
                "画像ファイル名": current_file,
                "①未融合": val_1,
                "②接触": val_2,
                "③融合中": val_3,
                "④完全融合": val_4
            }
            # バッファ初期化（なければ）
            if "buffered_entries" not in st.session_state:
                st.session_state.buffered_entries = []

            # 🔽 修正：正しいフォルダ名を参照して重複チェック
            st.session_state.buffered_entries = [
                e for e in st.session_state.buffered_entries
                if not (e["選択フォルダ"] == folder_for_this_image and e["画像ファイル名"] == current_file)
            ]
            st.session_state.buffered_entries.append(new_entry)

            # 入力リセット（オプション：ここは必要なら残す）
            st.session_state[f"val1_{current_file}"] = 0
            st.session_state[f"val2_{current_file}"] = 0
            st.session_state[f"val3_{current_file}"] = 0
            st.session_state[f"val4_{current_file}"] = 0

            # 5件で保存
            if len(st.session_state.buffered_entries) >= 5:
                flush_buffer_to_sheet()

            # 次へ
            st.session_state.index += 1
            st.rerun()

if st.sidebar.button("途中保存"):
    flush_buffer_to_sheet()
    st.stop()
