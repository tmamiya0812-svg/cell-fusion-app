import os

# 実行する Streamlit アプリの絶対パス（仮のパス）
file_path = r"C:\mix\googleapp\flashcard_google.py"

# 評価対象の画像フォルダの絶対パス（仮のパス）
folder_path = r"C:\mix\googleapp"

# Streamlit 実行 + 引数にフォルダパスを渡す
file_path = os.path.normpath(file_path)
folder_path = os.path.normpath(folder_path)
os.system(f'streamlit run "{file_path}" "{folder_path}"')
