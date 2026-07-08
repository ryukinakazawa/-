"""アップロードしたCSV/Excelの列を画面上で任意の項目にマッピングするための共通部品。

ソフトベンダーや自社運用によって列構成(ヘッダー)が異なっても、
画面上でどの列がどの項目かを指定すれば取り込めるようにする。
一度指定したマッピングは列構成のシグネチャ(ヘッダー名の組み合わせ)ごとに保存され、
次回以降は自動的に復元される。
"""
import hashlib

import pandas as pd
import streamlit as st

from lib import db

TargetField = tuple[str, str, bool]  # (キー, 表示ラベル, 必須かどうか)

NOT_USED = "(使用しない)"


def compute_header_signature(columns) -> str:
    normalized = sorted(str(c).strip() for c in columns)
    return hashlib.md5("|".join(normalized).encode("utf-8")).hexdigest()


def render_mapping_ui(
    df: pd.DataFrame,
    target_fields: list[TargetField],
    file_type: str,
    conn,
    form_key: str,
) -> tuple[dict, str, list[str]]:
    """列マッピングのUIを描画し、(mapping, header_signature, 未設定の必須項目一覧) を返す。"""
    signature = compute_header_signature(df.columns)
    saved_mapping = db.load_mapping(conn, file_type, signature) or {}

    if saved_mapping:
        st.info("以前と同じ列構成を検出したため、前回のマッピング設定を自動適用しました。内容を確認してください。")
    else:
        st.caption("各項目に対応する列を選択してください。取り込むデータに存在しない項目は「(使用しない)」のままにしてください。")

    options = [NOT_USED] + [str(c) for c in df.columns]
    mapping: dict[str, str] = {}

    cols = st.columns(2)
    for i, (key, label, required) in enumerate(target_fields):
        target_col = cols[i % 2]
        default_value = saved_mapping.get(key)
        default_index = options.index(default_value) if default_value in options else 0
        selected = target_col.selectbox(
            f"{label}{'(必須)' if required else ''}",
            options,
            index=default_index,
            key=f"{form_key}_{key}",
        )
        if selected != NOT_USED:
            mapping[key] = selected

    missing_required = [label for key, label, required in target_fields if required and key not in mapping]
    return mapping, signature, missing_required


def apply_mapping(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """マッピング設定に従って、指定項目キー名の列を持つDataFrameを作る。"""
    source_columns = list(mapping.values())
    result = df[source_columns].copy()
    result.columns = list(mapping.keys())
    return result
