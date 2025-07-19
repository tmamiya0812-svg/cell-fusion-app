import streamlit as st
import pandas as pd
import random
import re
import gspread
from google.oauth2.service_account import Credentials

# ====== Google Sheets 設定 ======
SHEET_ID = "1KUxQDhhnYS6tj4pFYAHwq9SzWxx3iDotTGXSzFUTU-s"
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
gc = gspread.authorize(credentials)
sheet = gc.open_by_key(SHEET_ID)

# ====== 必須列の定義 ======
required_cols = ["回答者", "親フォルダ", "時間", "選択フォルダ", "画像ファイル名", "①未融合", "②接触", "③融合中", "④完全融合"]
skip_cols = ["回答者", "親フォルダ", "時間", "選択フォルダ", "画像ファイル名", "スキップ理由"]

# ====== データの読み込み関数 ======
def sheet_to_df(ws_name, cols):
    try:
        ws = sheet.worksheet(ws_name)
        return pd.DataFrame(ws.get_all_records())
    except:
        return pd.DataFrame(columns=cols)

def df_to_sheet(df, ws_name):
    ws = sheet.worksheet(ws_name)
    ws.clear()
    if not df.empty:
        ws.update([df.columns.tolist()] + df.values.tolist())

# ====== ログイン認証 ======
USER_CREDENTIALS = {
    "mamiya": "a",
    "arai": "a",
    "yamazaki": "protoplast"
}

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

# ====== 評価データの取得 ======
username = st.session_state.username
combined_df = sheet_to_df("今回の評価", required_cols)
existing_df = combined_df.copy()
skip_df = sheet_to_df("スキップログ", skip_cols)
if "選択フォルダ" in skip_df.columns:
    skip_df = skip_df[~skip_df["選択フォルダ"].str.contains("_SKIPPED_IMAGES", na=False)]

image_list_df = sheet_to_df("画像リスト", ["フォルダ", "画像ファイル名", "画像URL"])
folder_names = sorted(image_list_df["フォルダ"].unique().tolist())

selected_folder = st.sidebar.selectbox("評価するフォルダを選んでください", folder_names)
folder_images = image_list_df[image_list_df["フォルダ"] == selected_folder]



if folder_images.empty:
    st.error("このフォルダには画像がありません")
    st.stop()

parent_folder_name = "mix"

# ====== 回答・スキップ済みの重複除外 ======
user_df = combined_df[combined_df["回答者"] == username].copy()
skip_pairs = set(zip(skip_df["選択フォルダ"], skip_df["画像ファイル名"]))
answered_pairs = set(zip(user_df["選択フォルダ"], user_df["画像ファイル名"]))
all_done = answered_pairs.union(skip_pairs)

# ====== セッション初期化 ======
if "image_files" not in st.session_state:
    all_files = folder_images[~folder_images["画像ファイル名"].isin([f for f1, f in all_done if f1 == selected_folder])]
    st.session_state.image_files = all_files.reset_index(drop=True)
    st.session_state.index = 0

if st.session_state.index >= len(st.session_state.image_files):
    st.success("このフォルダのすべての画像を評価しました")
    if st.button("終了"):
        if "buffered_entries" in st.session_state and st.session_state.buffered_entries:
            buffered_df = pd.DataFrame(st.session_state.buffered_entries)
            combined_df = pd.concat([existing_df, buffered_df], ignore_index=True)
            combined_df.drop_duplicates(subset=["回答者", "選択フォルダ", "画像ファイル名"], keep="last", inplace=True)

            summary = combined_df.groupby(["選択フォルダ", "時間"])[["①未融合", "②接触", "③融合中", "④完全融合"]].sum().reset_index()
            summary.insert(0, "一意ID", summary["選択フォルダ"] + "_" + summary["時間"])

            df_to_sheet(combined_df, "今回の評価")
            df_to_sheet(summary, "分類別件数")
            df_to_sheet(skip_df, "スキップログ")

        st.success("保存して終了しました。アプリを閉じてください。")
    st.stop()

# ====== 現在の画像表示 ======
row = st.session_state.image_files.iloc[st.session_state.index]
current_file = row["画像ファイル名"]
current_url = row["画像URL"]

st.progress((st.session_state.index + 1) / len(st.session_state.image_files))
st.image(current_url, use_container_width=True)

# ====== 分類入力 ======
st.markdown("### 各分類の個数を入力してください")
col1, col2, col3, col4 = st.columns(4)
with col1:
    val_1 = st.number_input("①未融合", min_value=0, max_value=1000, step=1, key=f"val1_{current_file}")
with col2:
    val_2 = st.number_input("②接触", min_value=0, max_value=1000, step=1, key=f"val2_{current_file}")
with col3:
    val_3 = st.number_input("③融合中", min_value=0, max_value=1000, step=1, key=f"val3_{current_file}")
with col4:
    val_4 = st.number_input("④完全融合", min_value=0, max_value=1000, step=1, key=f"val4_{current_file}")

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("← 戻る"):
        if st.session_state.index > 0:
            st.session_state.index -= 1
            st.rerun()

with col2:
    if st.button("スキップ"):
        match = re.search(r'(\d+min)', selected_folder)
        time_str = match.group(1) if match else "不明"
        skip_entry = {
            "回答者": username,
            "親フォルダ": parent_folder_name,
            "時間": time_str,
            "選択フォルダ": selected_folder,
            "画像ファイル名": current_file,
            "スキップ理由": "判別不能"
        }
        skip_df = pd.concat([skip_df, pd.DataFrame([skip_entry])], ignore_index=True)
        skip_df.drop_duplicates(subset=["回答者", "選択フォルダ", "画像ファイル名"], keep="last", inplace=True)

        st.session_state.index += 1
        st.rerun()

with col3:
    if st.button("進む →"):
        total = val_1 + val_2 + val_3 + val_4
        if total == 0:
            st.warning("少なくとも1つは分類してください")
        else:
            match = re.search(r'(\d+min)', selected_folder)
            time_str = match.group(1) if match else "不明"
            new_entry = {
                "回答者": username,
                "親フォルダ": parent_folder_name,
                "時間": time_str,
                "選択フォルダ": selected_folder,
                "画像ファイル名": current_file,
                "①未融合": val_1,
                "②接触": val_2,
                "③融合中": val_3,
                "④完全融合": val_4
            }

            if "buffered_entries" not in st.session_state:
                st.session_state.buffered_entries = []
            st.session_state.buffered_entries.append(new_entry)

            if len(st.session_state.buffered_entries) >= 5:
                buffered_df = pd.DataFrame(st.session_state.buffered_entries)
                combined_df = pd.concat([existing_df, buffered_df], ignore_index=True)
                combined_df.drop_duplicates(subset=["回答者", "選択フォルダ", "画像ファイル名"], keep="last", inplace=True)

                summary = combined_df.groupby(["選択フォルダ", "時間"])[["①未融合", "②接触", "③融合中", "④完全融合"]].sum().reset_index()
                summary.insert(0, "一意ID", summary["選択フォルダ"] + "_" + summary["時間"])

                df_to_sheet(combined_df, "今回の評価")
                df_to_sheet(summary, "分類別件数")
                df_to_sheet(skip_df, "スキップログ")

                existing_df = combined_df.copy()
                st.session_state.buffered_entries = []

            st.session_state.index += 1
            st.rerun()
