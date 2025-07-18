import os
import sys
import streamlit as st
import pandas as pd
from PIL import Image
import random
import re
import shutil
from openpyxl import load_workbook
# === Google Sheets 認証関連（コードの上の方で）===
import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_ID = "1yQuifGNG8e77ka5HlJariXxgqPffrIviDZKgmS9FGCg"
CREDENTIAL_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")


scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIAL_FILE, scope)
gc = gspread.authorize(credentials)
sheet = gc.open_by_key(SHEET_ID)

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

# ログファイルとスキップログ用の列
required_cols = ["回答者", "親フォルダ", "時間", "選択フォルダ", "画像ファイル名", "①未融合", "②接触", "③融合中", "④完全融合"]
skip_cols = ["回答者", "親フォルダ", "時間", "選択フォルダ", "画像ファイル名", "スキップ理由"]


# === 読み込み処理（元のif文の代わりに）===
combined_df = sheet_to_df("今回の評価", required_cols)
existing_df = combined_df.copy()  # 読み込み直後に追加
skip_df = sheet_to_df("スキップログ", skip_cols)

if "選択フォルダ" in skip_df.columns:
    skip_df = skip_df[~skip_df["選択フォルダ"].str.contains("_SKIPPED_IMAGES", na=False)]
else:
    skip_df = pd.DataFrame(columns=skip_cols)


# ====== 認証設定 ======
USER_CREDENTIALS = {
    "mamiya": "protoplast",
    "arai": "a",
    "yamazaki": "protoplast"
}

# ====== Streamlit 設定 ======
st.set_page_config(page_title="融合度評価", layout="centered")
st.title("融合度評価 - フラッシュカード")

# ====== ログイン認証 ======
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

# ====== 認証後の処理 ======
username = st.session_state.username

# 起動元の引数から評価対象フォルダ取得
folder_args = [arg for arg in sys.argv if not arg.endswith(".py") and not arg.startswith("--")]
if not folder_args:
    st.error("このアプリは launch_flashcard.py から起動してください。")
    st.stop()

selected_dir = folder_args[0]
parent_folder_name = os.path.basename(os.path.abspath(selected_dir))
subfolders = [
    f for f in os.listdir(selected_dir)
    if os.path.isdir(os.path.join(selected_dir, f))
    and not f.startswith("_NG")
    and f != "_SKIPPED_IMAGES"
]

if not subfolders:
    st.error("指定されたフォルダにサブフォルダがありません")
    st.stop()


# スキップ画像保存先
skip_folder = os.path.join(selected_dir, "_SKIPPED_IMAGES")
os.makedirs(skip_folder, exist_ok=True)


user_df = combined_df[combined_df["回答者"] == username].copy()
skip_pairs = set(zip(skip_df["選択フォルダ"], skip_df["画像ファイル名"]))
answered_pairs = set(zip(user_df["選択フォルダ"], user_df["画像ファイル名"]))
all_skipped_and_answered = answered_pairs.union(skip_pairs)

# セッション初期化
if "current_folder_index" not in st.session_state:
    st.session_state.current_folder_index = 0
if "index" not in st.session_state:
    st.session_state.index = 0
if "finished_folders" not in st.session_state:
    st.session_state.finished_folders = set()
if "image_files" not in st.session_state:
    st.session_state.image_files = None

# 残りフォルダ処理
remaining_folders = [f for f in subfolders if f not in st.session_state.finished_folders]
if not remaining_folders:
    st.success("すべてのサブフォルダの評価が完了しました。お疲れさまでした。")
    st.stop()

selected_folder = remaining_folders[st.session_state.current_folder_index % len(remaining_folders)]
folder_path = os.path.join(selected_dir, selected_folder)

if st.session_state.image_files is None:
    all_images = [f for f in os.listdir(folder_path) if f.lower().endswith(".png")]
    images_to_evaluate = [f for f in all_images if (selected_folder, f) not in all_skipped_and_answered]
    random.shuffle(images_to_evaluate)
    st.session_state.image_files = images_to_evaluate
    st.session_state.index = 0

if not st.session_state.image_files or st.session_state.index >= len(st.session_state.image_files):
    st.success("このフォルダのすべての画像を評価しました")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("続ける"):
            st.session_state.finished_folders.add(selected_folder)
            st.session_state.current_folder_index += 1
            st.session_state.index = 0
            st.session_state.image_files = None
            st.rerun()
    with col2:
        if st.button("中断して終了"):
            # 終了前に残っているデータがあれば保存
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

current_file = st.session_state.image_files[st.session_state.index]
current_path = os.path.join(folder_path, current_file)
st.progress((st.session_state.index + 1) / len(st.session_state.image_files))
st.image(Image.open(current_path), use_container_width=True)

# 入力フォーム
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
        skipped_path = os.path.join(skip_folder, f"{selected_folder}_{current_file}")
        try:
            shutil.copy2(current_path, skipped_path)
        except Exception as e:
            st.error(f"スキップ画像の保存に失敗しました: {e}")
        else:
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
            summary = combined_df.groupby(["選択フォルダ", "時間"])[["①未融合", "②接触", "③融合中", "④完全融合"]].sum().reset_index()
            summary.insert(0, "一意ID", summary["選択フォルダ"] + "_" + summary["時間"])
            

            st.info(f"画像をスキップしました：{current_file}")
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
            new_df = pd.DataFrame([new_entry])
            combined_df.drop_duplicates(subset=["回答者", "選択フォルダ", "画像ファイル名"], keep="last", inplace=True)
            summary = combined_df.groupby(["選択フォルダ","時間"])[["①未融合", "②接触", "③融合中", "④完全融合"]].sum().reset_index()
            summary.insert(0, "一意ID", summary["選択フォルダ"]+"_"+summary["時間"])
            # セッション内で一時的に保存するバッファ
            if "buffered_entries" not in st.session_state:
                st.session_state.buffered_entries = []

            st.session_state.buffered_entries.append(new_entry)

            # 5件たまったらまとめて書き込み
            if len(st.session_state.buffered_entries) >= 5:
                buffered_df = pd.DataFrame(st.session_state.buffered_entries)
                combined_df = pd.concat([existing_df, buffered_df], ignore_index=True)
                combined_df.drop_duplicates(subset=["回答者", "選択フォルダ", "画像ファイル名"], keep="last", inplace=True)
               
                summary = combined_df.groupby(["選択フォルダ", "時間"])[["①未融合", "②接触", "③融合中", "④完全融合"]].sum().reset_index()
                summary.insert(0, "一意ID", summary["選択フォルダ"] + "_" + summary["時間"])

                # Google Sheets へ保存
                df_to_sheet(combined_df, "今回の評価")
                df_to_sheet(summary, "分類別件数")
                df_to_sheet(skip_df, "スキップログ")

                # ローカルの DataFrame も更新しておく
                existing_df = combined_df.copy()
                st.session_state.buffered_entries = []

            st.session_state.index += 1
            st.rerun()
