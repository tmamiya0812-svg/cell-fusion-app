import streamlit as st
import pandas as pd
import random
import re
import gspread
from google.oauth2.service_account import Credentials

# ====== Google Sheets 設定 ======
IMAGE_SHEET_ID = "1KUxQDhhnYS6tj4pFYAHwq9SzWxx3iDotTGXSzFUTU-s"
LOG_SHEET_ID = "1yQuifGNG8e77ka5HlJariXxgqPffrIviDZKgmS9FGCg"
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
gc = gspread.authorize(credentials)
image_sheet = gc.open_by_key(IMAGE_SHEET_ID)
log_sheet = gc.open_by_key(LOG_SHEET_ID)


# ====== 必須列の定義 ======
required_cols = ["回答者", "親フォルダ", "時間", "選択フォルダ", "画像ファイル名", "①未融合", "②接触", "③融合中", "④完全融合"]
skip_cols = ["回答者", "親フォルダ", "時間", "選択フォルダ", "画像ファイル名", "スキップ理由"]



@st.cache_data(ttl=60)
def load_ws_data(sheet_id: str, ws_name: str, header_cols: list) -> pd.DataFrame:
    gc = gspread.authorize(credentials)
    sheet = gc.open_by_key(sheet_id)
    try:
        ws = sheet.worksheet(ws_name)
        return pd.DataFrame(ws.get_all_records())
    except gspread.exceptions.WorksheetNotFound:
        sheet.add_worksheet(title=ws_name, rows="1000", cols=str(len(header_cols)))
        ws = sheet.worksheet(ws_name)
        ws.append_row(header_cols)
        return pd.DataFrame(columns=header_cols)

def df_to_sheet_to(sheet_obj, df, ws_name):
    ws = sheet_obj.worksheet(ws_name)
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
st.sidebar.markdown(f"**ログイン中:** `{username}`")
combined_df = load_ws_data(LOG_SHEET_ID, "今回の評価", required_cols)
existing_df = combined_df.copy()
skip_df = load_ws_data(LOG_SHEET_ID, "スキップログ", skip_cols)
if "選択フォルダ" in skip_df.columns:
    skip_df = skip_df[~skip_df["選択フォルダ"].str.contains("_SKIPPED_IMAGES", na=False)]
image_list_df = load_ws_data(IMAGE_SHEET_ID, "画像リスト", ["フォルダ", "画像ファイル名", "画像URL"])



folder_names = sorted(image_list_df["フォルダ"].unique().tolist())

# === 評価済み・スキップ済み画像の組み合わせ取得 ===
answered_pairs = set(zip(combined_df["選択フォルダ"], combined_df["画像ファイル名"]))
if "選択フォルダ" in skip_df.columns and "画像ファイル名" in skip_df.columns:
    skipped_pairs = set(zip(skip_df["選択フォルダ"], skip_df["画像ファイル名"]))
else:
    skipped_pairs = set()
done_pairs = answered_pairs.union(skipped_pairs)


# === フォルダごとの未評価画像が存在するか判定して、完了フォルダを除外 ===
remaining_folders = []
for folder in folder_names:
    folder_df = image_list_df[image_list_df["フォルダ"] == folder]
    all_images = set(zip(folder_df["フォルダ"], folder_df["画像ファイル名"]))
    if not all_images.issubset(done_pairs):
        remaining_folders.append(folder)


if not remaining_folders:
    st.success("すべてのフォルダを評価しました！")
    st.stop()

selected_folder = random.choice(remaining_folders)

if st.sidebar.button("途中保存"):
    if "buffered_entries" in st.session_state and st.session_state.buffered_entries:
        buffered_df = pd.DataFrame(st.session_state.buffered_entries)
        combined_df = pd.concat([existing_df, buffered_df], ignore_index=True)
        combined_df.drop_duplicates(subset=["回答者", "選択フォルダ", "画像ファイル名"], keep="last", inplace=True)

        summary = combined_df.groupby(["選択フォルダ", "時間"])[["①未融合", "②接触", "③融合中", "④完全融合"]].sum().reset_index()
        summary.insert(0, "一意ID", summary["選択フォルダ"] + "_" + summary["時間"])

        # 評価保存前
        st.sidebar.markdown("💾 保存直前のcombined_df 件数: " + str(len(combined_df)))
        st.sidebar.markdown("🔍 現在の combined_df 件数: " + str(len(combined_df)))
        st.sidebar.markdown("📄 最新の combined_df の一部:")
        st.sidebar.dataframe(combined_df.tail(5))

        
        df_to_sheet_to(log_sheet, combined_df, "今回の評価")
        df_to_sheet_to(log_sheet, summary, "分類別件数")
        df_to_sheet_to(log_sheet, skip_df, "スキップログ")

        st.session_state.buffered_entries = []
        st.sidebar.success("一時保存しました")

folder_images = image_list_df[image_list_df["フォルダ"] == selected_folder]



if folder_images.empty:
    st.error("このフォルダには画像がありません")
    st.stop()

parent_folder_name = "mix"

# ====== 回答・スキップ済みの重複除外 ======
user_df = combined_df[combined_df["回答者"] == username].copy()

st.sidebar.markdown(f"👤 フィルタ後 user_df 件数: {len(user_df)}")
st.sidebar.markdown(f"🧪 現在の username: `{username}`")


# skip_dfに必要な列がある場合だけ処理する
if "選択フォルダ" in skip_df.columns and "画像ファイル名" in skip_df.columns:
    skip_pairs = set(zip(skip_df["選択フォルダ"], skip_df["画像ファイル名"]))
else:
    skip_pairs = set()

# combined_dfには常にある前提（なければ同様にチェック追加が必要）
answered_pairs = set(zip(user_df["選択フォルダ"], user_df["画像ファイル名"]))
all_done = answered_pairs.union(skip_pairs)


# ====== セッション初期化 ======
if "image_files" not in st.session_state:
    folder_images["pair"] = list(zip(folder_images["フォルダ"], folder_images["画像ファイル名"]))
    filtered_images = folder_images[~folder_images["pair"].isin(all_done)].drop(columns=["pair"])
    st.session_state.image_files = filtered_images.reset_index(drop=True)
    st.session_state.index = 0

if st.session_state.index >= len(st.session_state.image_files):
    st.success("このフォルダのすべての画像を評価しました")

    # 評価結果を保存
    if "buffered_entries" in st.session_state and st.session_state.buffered_entries:
        buffered_df = pd.DataFrame(st.session_state.buffered_entries)
        combined_df = pd.concat([existing_df, buffered_df], ignore_index=True)
        combined_df.drop_duplicates(subset=["回答者", "選択フォルダ", "画像ファイル名"], keep="last", inplace=True)

        summary = combined_df.groupby(["選択フォルダ", "時間"])[["①未融合", "②接触", "③融合中", "④完全融合"]].sum().reset_index()
        summary.insert(0, "一意ID", summary["選択フォルダ"] + "_" + summary["時間"])

        df_to_sheet_to(log_sheet, combined_df, "今回の評価")
        df_to_sheet_to(log_sheet, summary, "分類別件数")
        df_to_sheet_to(log_sheet, skip_df, "スキップログ")

        existing_df = combined_df.copy()
        
        st.sidebar.markdown(f"📥 読み込み直後の existing_df 件数: {len(existing_df)}")

        st.session_state.buffered_entries = []
        st.sidebar.success("保存しました（フォルダ終了時）")

    # --- 次のフォルダを選ぶ ---
    answered_pairs = set(zip(combined_df["選択フォルダ"], combined_df["画像ファイル名"]))
    if "選択フォルダ" in skip_df.columns and "画像ファイル名" in skip_df.columns:
        skipped_pairs = set(zip(skip_df["選択フォルダ"], skip_df["画像ファイル名"]))
    else:
        skipped_pairs = set()
    done_pairs = answered_pairs.union(skipped_pairs)

    # フォルダの再選択
    remaining_folders = []
    for folder in folder_names:
        folder_df = image_list_df[image_list_df["フォルダ"] == folder]
        all_images = set(zip(folder_df["フォルダ"], folder_df["画像ファイル名"]))
        if not all_images.issubset(done_pairs):
            remaining_folders.append(folder)

    if not remaining_folders:
        st.success("すべてのフォルダを評価しました！")
        st.stop()

    # フォルダと画像を再初期化
    selected_folder = random.choice(remaining_folders)
    folder_images = image_list_df[image_list_df["フォルダ"] == selected_folder]
    all_done = answered_pairs.union(skipped_pairs)
    folder_images["pair"] = list(zip(folder_images["フォルダ"], folder_images["画像ファイル名"]))
    filtered_images = folder_images[~folder_images["pair"].isin(all_done)].drop(columns=["pair"])
    st.session_state.image_files = filtered_images.reset_index(drop=True)
    st.session_state.index = 0
    st.rerun()

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

                df_to_sheet_to(log_sheet, combined_df, "今回の評価")
                df_to_sheet_to(log_sheet, summary, "分類別件数")
                df_to_sheet_to(log_sheet, skip_df, "スキップログ")


                existing_df = combined_df.copy()
                st.session_state.buffered_entries = []

            st.session_state.index += 1
            st.rerun()
