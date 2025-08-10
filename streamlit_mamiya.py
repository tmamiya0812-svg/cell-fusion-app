# -*- coding: utf-8 -*-
# Streamlit: 融合度評価（Google Sheets最適化・読み取り削減・正規化フル版）

import streamlit as st
import pandas as pd
import random
import re
import time
import gspread
from google.oauth2.service_account import Credentials

# =========================
# 基本設定
# =========================
st.set_page_config(page_title="融合度評価", layout="centered")
st.title("融合度評価")

# === Google Sheets IDs ===
IMAGE_SHEET_ID = "1gDGW6B3Sj9piVHN5vEvQ9JlMp2BjGhdnyL32R7MdF8I"
LOG_SHEET_ID   = "17xAIAz6xIoM9eZHona-GyMMdM5zku4cRtUXCud5Rc5Y"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

# === 列定義 ===
required_cols = ["回答者", "親フォルダ", "時間", "選択フォルダ", "画像ファイル名", "①未融合", "②接触", "③融合中", "④完全融合"]
skip_cols     = ["回答者", "親フォルダ", "時間", "選択フォルダ", "画像ファイル名", "スキップ理由"]
image_cols    = ["フォルダ", "画像ファイル名", "画像URL"]

# === 正規化設定（必要なら接頭辞を追加） ===
STRIP_PREFIXES = ("homogeneous_mix/",)

def norm_folder(s: str) -> str:
    """選択フォルダの表記ゆれを正規化（接頭辞除去・区切り統一・前後空白除去）"""
    if not isinstance(s, str):
        return s
    s = s.replace("\\", "/").strip()
    for p in STRIP_PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
    return s

# =========================
# Google クライアント（1回作成）
# =========================
@st.cache_resource
def get_clients():
    credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=SCOPES)
    gc = gspread.authorize(credentials)
    image_sheet = gc.open_by_key(IMAGE_SHEET_ID)
    log_sheet   = gc.open_by_key(LOG_SHEET_ID)
    return gc, image_sheet, log_sheet

gc, image_sheet, log_sheet = get_clients()

# =========================
# ユーティリティ
# =========================
def _to_df(values, header_expected):
    """A1形式の値配列 -> DataFrame。欠損列は補完、余剰列は落とす。"""
    if not values or len(values) == 0:
        return pd.DataFrame(columns=header_expected)
    header = values[0]
    rows   = values[1:] if len(values) > 1 else []
    df = pd.DataFrame(rows, columns=header)
    for c in header_expected:
        if c not in df.columns:
            df[c] = ""
    return df[header_expected]

def batch_get_safe(sheet, ranges, retries=3, backoff=1.5):
    """values_batch_get を使った一括取得（429/5xxは軽い指数バックオフ）"""
    last_err = None
    for i in range(retries):
        try:
            resp = sheet.values_batch_get(ranges=ranges)
            value_ranges = resp.get("valueRanges", [])
            return [vr.get("values", []) for vr in value_ranges]
        except Exception as e:
            last_err = e
            time.sleep((backoff ** i))
    raise last_err

def ensure_ws(sheet_obj, ws_name, header_cols):
    """ワークシート存在保証（ヘッダーのみ作成）。読み取りはしない。"""
    try:
        ws = sheet_obj.worksheet(ws_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sheet_obj.add_worksheet(title=ws_name, rows="1000", cols=str(len(header_cols)))
        ws.update("A1", [header_cols])  # ヘッダー作成
    return ws

def append_df_to_sheet(sheet_obj, df: pd.DataFrame, ws_name: str):
    """append-only（読まない）。"""
    if df.empty:
        return
    ws = ensure_ws(sheet_obj, ws_name, df.columns.tolist())
    ws.append_rows(df.values.tolist(), value_input_option="USER_ENTERED")

def time_from_folder(folder_name: str) -> str:
    m = re.search(r'(\d+min)', folder_name)
    return m.group(1) if m else "不明"
def compute_remaining(image_list_df, combined_df, skip_df, username):
    """サーバ回答・スキップ・セッション回答・セッション内スキップを除いた残り枚数を計算"""
    user_df = combined_df[combined_df["回答者"] == username].copy()

    answered_server = set(zip(user_df["選択フォルダ_norm"], user_df["画像ファイル名"]))
    skipped_server  = set(zip(skip_df["選択フォルダ_norm"],  skip_df["画像ファイル名"]))
    answered_local  = st.session_state.get("answered_pairs_session", set())

    # ★ 追加: セッション内のスキップ（まだシートを再読込していない分）も除外
    # skip_keys は (回答者, 選択フォルダ_norm, 画像ファイル名) のタプル
    local_skipped = set(
        (folder_norm, img)
        for (user, folder_norm, img) in st.session_state.get("skip_keys", set())
        if user == username
    )

    done_pairs = answered_server.union(skipped_server).union(answered_local).union(local_skipped)

    tmp = image_list_df.copy()
    tmp["pair_norm"] = list(zip(tmp["フォルダ_norm"], tmp["画像ファイル名"]))
    tmp["done"] = tmp["pair_norm"].isin(done_pairs)

    remaining_total = int((~tmp["done"]).sum())
    remaining_by_folder = (
        tmp.groupby("フォルダ_norm")["done"]
           .apply(lambda s: int((~s).sum()))
           .to_dict()
    )
    return remaining_total, remaining_by_folder



# =========================
# テーブル一括読取（キャッシュ）
# =========================
@st.cache_data(ttl=600)
def load_all_tables():
    # IMAGE_SHEET: 画像リスト
    img_vals = batch_get_safe(image_sheet, ["画像リスト!A1:C"])
    # LOG_SHEET: 今回の評価 + スキップログ
    log_vals = batch_get_safe(log_sheet, ["今回の評価!A1:I", "スキップログ!A1:F"])
    img_df  = _to_df(img_vals[0] if img_vals else [], image_cols)
    eval_df = _to_df(log_vals[0] if len(log_vals) > 0 else [], required_cols)
    skip_df = _to_df(log_vals[1] if len(log_vals) > 1 else [], skip_cols)

    # ★ 正規化列を付与
    img_df["フォルダ_norm"] = img_df["フォルダ"].map(norm_folder)
    eval_df["選択フォルダ_norm"] = eval_df["選択フォルダ"].map(norm_folder)
    skip_df["選択フォルダ_norm"] = skip_df["選択フォルダ"].map(norm_folder)
    return img_df, eval_df, skip_df

# 初回ロード（以後は手動リロードまで再読取しない）
image_list_df, combined_df, skip_df = load_all_tables()

# =========================
# ログイン
# =========================
USER_CREDENTIALS = {"mamiya": "a", "arai": "a", "yamazaki": "protoplast"}

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

# =========================
# セッション内メモリ（重複防止）
# =========================
# スキップキー（回答者×選択フォルダ_norm×画像ファイル名）
if "skip_keys" not in st.session_state:
    st.session_state.skip_keys = set(zip(skip_df["回答者"], skip_df["選択フォルダ_norm"], skip_df["画像ファイル名"]))

# セッションで新規に回答済みの (選択フォルダ_norm, 画像ファイル名) を追跡
if "answered_pairs_session" not in st.session_state:
    st.session_state.answered_pairs_session = set()

# バッファ
if "buffered_entries" not in st.session_state:
    st.session_state.buffered_entries = []

# =========================
# サイドバー：運用ツール（手動発火）
# =========================
with st.sidebar.expander("データ更新・保守", expanded=False):
    if st.button("シートを再読み込み"):
        st.cache_data.clear()
        image_list_df, combined_df, skip_df = load_all_tables()
        # セッション内キー再構築（正規化版）
        st.session_state.skip_keys = set(zip(skip_df["回答者"], skip_df["選択フォルダ_norm"], skip_df["画像ファイル名"]))
        st.success("最新データに更新しました")

with st.sidebar.expander("セル使用量をチェック（押した時だけ）", expanded=False):
    if st.button("今すぐチェックする"):
        try:
            total_cells = 0
            details = []
            for ws in log_sheet.worksheets():
                cells = ws.row_count * ws.col_count
                total_cells += cells
                details.append(f"{ws.title}: {ws.row_count} rows × {ws.col_count} cols = {cells:,} cells")
            st.write("### LOG_SHEET 全体セル数:", f"{total_cells:,}")
            st.write("\n".join(details))
        except Exception as e:
            st.error(f"セル使用量チェックでエラー: {e}")

with st.sidebar.expander("巨大シートの最適化（手動）", expanded=False):
    def shrink_to_minimal(ws, keep_cols: list):
        try:
            used_rows = len(ws.get_all_values())  # ここは手動時のみ呼ぶ
            if used_rows == 0:
                used_rows = 1
            ws.resize(rows=used_rows + 100, cols=len(keep_cols))
            ws.update('A1', [keep_cols])
            st.success(f"{ws.title}: {used_rows}行, {len(keep_cols)}列 に最適化しました。")
        except Exception as e:
            st.error(f"{ws.title} 最適化エラー: {e}")

    if st.button("スキップログを最適化（6列）"):
        try:
            ws = log_sheet.worksheet("スキップログ")
            shrink_to_minimal(ws, skip_cols)
        except Exception as e:
            st.error(f"スキップログ取得エラー: {e}")

    if st.button("今回の評価を最適化（定義列数）"):
        try:
            ws = log_sheet.worksheet("今回の評価")
            shrink_to_minimal(ws, required_cols)
        except Exception as e:
            st.error(f"今回の評価取得エラー: {e}")

with st.sidebar.expander("スキップログ重複クリーニング（手動）", expanded=False):
    st.markdown("**回答者×選択フォルダ×画像ファイル名（選択フォルダは正規化）** で重複削除（最後の1件を残す）。")
    if st.button("重複削除を実行"):
        try:
            ws = log_sheet.worksheet("スキップログ")
            vals = ws.get_all_values()
            if not vals:
                st.info("スキップログが空です。")
            else:
                header, data = vals[0], vals[1:]
                df = pd.DataFrame(data, columns=header)
                for c in skip_cols:
                    if c not in df.columns:
                        df[c] = ""
                df = df[skip_cols]
                # 正規化キーを追加
                df["選択フォルダ_norm"] = df["選択フォルダ"].map(norm_folder)
                df_dedup = df.drop_duplicates(subset=["回答者","選択フォルダ_norm","画像ファイル名"], keep="last").copy()
                # 書き戻しは正規化名で統一
                df_dedup["選択フォルダ"] = df_dedup["選択フォルダ_norm"]
                df_dedup = df_dedup[skip_cols]

                ws.clear()
                ws.resize(rows=1, cols=len(skip_cols))
                ws.update("A1", [skip_cols])
                CHUNK = 1000
                rows = df_dedup.values.tolist()
                for i in range(0, len(rows), CHUNK):
                    ws.append_rows(rows[i:i+CHUNK], value_input_option="USER_ENTERED")

                # キャッシュ＆セッション更新
                st.cache_data.clear()
                _, _, skip_df = load_all_tables()
                st.session_state.skip_keys = set(zip(skip_df["回答者"], skip_df["選択フォルダ_norm"], skip_df["画像ファイル名"]))
                st.success(f"重複削除（正規化）完了: {len(df)} → {len(df_dedup)} 行")
        except Exception as e:
            st.error(f"重複クリーニング中のエラー: {e}")

with st.sidebar.expander("今回の評価 重複クリーニング（手動）", expanded=False):
    st.markdown("**回答者×選択フォルダ×画像ファイル名（選択フォルダは正規化）** で重複削除（最後の1件を残す）。")
    if st.button("今回の評価の重複削除を実行"):
        try:
            ws = log_sheet.worksheet("今回の評価")
            vals = ws.get_all_values()
            if not vals:
                st.info("今回の評価が空です。")
            else:
                header, data = vals[0], vals[1:]
                df = pd.DataFrame(data, columns=header)
                for c in required_cols:
                    if c not in df.columns:
                        df[c] = ""
                df = df[required_cols]
                # 正規化キーを追加
                df["選択フォルダ_norm"] = df["選択フォルダ"].map(norm_folder)
                df_dedup = df.drop_duplicates(subset=["回答者","選択フォルダ_norm","画像ファイル名"], keep="last").copy()
                # 書き戻しは正規化名で統一
                df_dedup["選択フォルダ"] = df_dedup["選択フォルダ_norm"]
                df_dedup = df_dedup[required_cols]

                ws.clear()
                ws.resize(rows=1, cols=len(required_cols))
                ws.update("A1", [required_cols])
                CHUNK = 1000
                rows = df_dedup.values.tolist()
                for i in range(0, len(rows), CHUNK):
                    ws.append_rows(rows[i:i+CHUNK], value_input_option="USER_ENTERED")

                # 再読込
                st.cache_data.clear()
                image_list_df, combined_df, skip_df = load_all_tables()
                st.success(f"重複削除（正規化）完了: {len(df)} → {len(df_dedup)} 行")
        except Exception as e:
            st.error(f"重複クリーニング中のエラー: {e}")

# =========================
# 評価フロー構築
# =========================
# 画像リストが空なら停止
if image_list_df.empty:
    st.warning("画像リストが空です。画像リストシートを確認してください。")
    st.stop()

# フォルダ順（最初の一回だけランダム）→ 正規化名で管理
if "folder_order" not in st.session_state:
    all_folders = image_list_df["フォルダ_norm"].dropna().unique().tolist()
    random.shuffle(all_folders)
    st.session_state.folder_order = all_folders
    st.session_state.folder_index = 0

folder_names = st.session_state.folder_order
if st.session_state.folder_index >= len(folder_names):
    # 最後にバッファ吐き出し
    if st.session_state.buffered_entries:
        buffered_df = pd.DataFrame(st.session_state.buffered_entries)[required_cols]
        append_df_to_sheet(log_sheet, buffered_df, "今回の評価")
        st.session_state.buffered_entries = []
    st.success("すべてのフォルダを評価しました！")
    st.stop()

selected_folder_norm = folder_names[st.session_state.folder_index]
# === 残り枚数（全体 / 現在フォルダ） ===
with st.sidebar.expander("進捗（残り枚数）", expanded=True):
    try:
        remaining_total, remaining_by_folder = compute_remaining(image_list_df, combined_df, skip_df, username)
        st.metric("全体の残り", remaining_total)
        st.metric("このフォルダの残り", remaining_by_folder.get(selected_folder_norm, 0))

        # 残りが多い順に上位だけ（例：10件）を簡易表示
        top_items = sorted(remaining_by_folder.items(), key=lambda x: x[1], reverse=True)[:10]
        if top_items:
            st.write("残りが多いフォルダ（上位10）")
            for fname, rem in top_items:
                st.write(f"- {fname}: {rem}")
        else:
            st.caption("未評価はありません。")
    except Exception as e:
        st.error(f"残り枚数の計算でエラー: {e}")

# 表示は元のフォルダ列を使いつつ、選択は_normで絞る
folder_images = image_list_df[image_list_df["フォルダ_norm"] == selected_folder_norm].copy()

# 既存（サーバ）回答 + セッション中の新規回答 + スキップ を正規化キーで除外
user_df = combined_df[combined_df["回答者"] == username].copy()
answered_pairs_server = set(zip(user_df["選択フォルダ_norm"], user_df["画像ファイル名"]))
answered_pairs_local  = st.session_state.answered_pairs_session
skipped_pairs_server  = set(zip(skip_df["選択フォルダ_norm"], skip_df["画像ファイル名"]))
done_pairs = answered_pairs_server.union(answered_pairs_local).union(skipped_pairs_server)

# 正規化キーで判定
folder_images["pair_norm"] = list(zip(folder_images["フォルダ_norm"], folder_images["画像ファイル名"]))
filtered_images = folder_images[~folder_images["pair_norm"].isin(done_pairs)].drop(columns=["pair_norm"]).reset_index(drop=True)

# 対象が空なら次フォルダへ
if filtered_images.empty:
    st.session_state.folder_index += 1
    st.rerun()

# セッションに現在フォルダの画像一覧を保持
if "image_files" not in st.session_state:
    st.session_state.image_files = filtered_images
    st.session_state.index = 0

# 範囲外なら次フォルダへ（バッファ吐き出しも）
if st.session_state.index >= len(st.session_state.image_files):
    if st.session_state.buffered_entries:
        buffered_df = pd.DataFrame(st.session_state.buffered_entries)[required_cols]
        append_df_to_sheet(log_sheet, buffered_df, "今回の評価")
        st.session_state.buffered_entries = []
    st.session_state.folder_index += 1
    st.session_state.pop("image_files", None)
    st.session_state.pop("index", None)
    st.rerun()

# =========================
# 1枚表示 & 入力UI
# =========================
row = st.session_state.image_files.iloc[st.session_state.index]
current_file = row["画像ファイル名"]
current_url  = row["画像URL"]
folder_for_this_image = row["フォルダ"]          # 表示・時間抽出用（元名）
folder_norm_this      = row["フォルダ_norm"]     # 判定・保存用（正規化名）

st.progress((st.session_state.index + 1) / len(st.session_state.image_files))
st.image(current_url, use_container_width=True)

col1, col2, col3, col4 = st.columns(4)
val_1 = col1.number_input("\u2460未融合", min_value=0, max_value=1000, step=1, key=f"val1_{current_file}")
val_2 = col2.number_input("\u2461接触",   min_value=0, max_value=1000, step=1, key=f"val2_{current_file}")
val_3 = col3.number_input("\u2462融合中", min_value=0, max_value=1000, step=1, key=f"val3_{current_file}")
val_4 = col4.number_input("\u2463完全融合", min_value=0, max_value=1000, step=1, key=f"val4_{current_file}")

colA, colB, colC = st.columns(3)

# 戻る
with colA:
    if st.button("← 戻る"):
        if st.session_state.index > 0:
            st.session_state.index -= 1
            st.rerun()

# スキップ
with colB:
    if st.button("スキップ"):
        key = (username, folder_norm_this, current_file)  # 正規化キーで管理
        if key in st.session_state.skip_keys:
            st.info("この画像は既にスキップ済みです。")
        else:
            skip_entry = {
                "回答者": username,
                "親フォルダ": "mix",
                "時間": time_from_folder(folder_for_this_image),
                "選択フォルダ": folder_norm_this,   # ★正規化名で保存
                "画像ファイル名": current_file,
                "スキップ理由": "判別不能"
            }
            single_df = pd.DataFrame([skip_entry])[skip_cols]
            append_df_to_sheet(log_sheet, single_df, "スキップログ")
            # ローカル状態更新（再読取しない）
            st.session_state.skip_keys.add(key)
        st.session_state.index += 1
        st.rerun()

# 進む
with colC:
    if st.button("進む →"):
        if val_1 + val_2 + val_3 + val_4 == 0:
            st.warning("少なくとも1つは分類してください")
        else:
            new_entry = {
                "回答者": username,
                "親フォルダ": "mix",
                "時間": time_from_folder(folder_for_this_image),  # 元名から抽出でOK
                "選択フォルダ": folder_norm_this,                 # ★正規化名で保存
                "画像ファイル名": current_file,
                "①未融合": val_1,
                "②接触": val_2,
                "③融合中": val_3,
                "④完全融合": val_4
            }
            # 同一画像の重複をバッファ内で除去してから追加（正規化キーで判定）
            st.session_state.buffered_entries = [
                e for e in st.session_state.buffered_entries
                if not (e["選択フォルダ"] == folder_norm_this and e["画像ファイル名"] == current_file)
            ]
            st.session_state.buffered_entries.append(new_entry)

            # セッション内回答セットも更新（正規化名で）
            st.session_state.answered_pairs_session.add((folder_norm_this, current_file))

            # 入力リセット
            for i in range(1, 5):
                k = f"val{i}_{current_file}"
                if k in st.session_state:
                    del st.session_state[k]

            # 10件で保存（書込み回数を抑制）
            if len(st.session_state.buffered_entries) >= 10:
                buffered_df = pd.DataFrame(st.session_state.buffered_entries)[required_cols]
                append_df_to_sheet(log_sheet, buffered_df, "今回の評価")
                st.session_state.buffered_entries = []
                st.sidebar.success("保存しました（append-only）")

            st.session_state.index += 1
            st.rerun()

# 途中保存
if st.sidebar.button("途中保存"):
    if st.session_state.buffered_entries:
        buffered_df = pd.DataFrame(st.session_state.buffered_entries)[required_cols]
        append_df_to_sheet(log_sheet, buffered_df, "今回の評価")
        st.session_state.buffered_entries = []
        st.success("途中保存しました（append-only）")
    else:
        st.info("保存対象はありません。")
