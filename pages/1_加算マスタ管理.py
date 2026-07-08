import pandas as pd
import streamlit as st

from lib import db, mapping

st.set_page_config(page_title="加算マスタ管理", page_icon="🗂️", layout="wide")

conn = db.get_conn()
db.init_db(conn)

st.title("加算マスタ管理")
st.write(
    "各事業所が取得している(算定基準を満たすとして届け出ている)加算の一覧を取り込み・管理します。"
    "既存のExcel等の一覧をそのままアップロードし、画面上でどの列がどの項目かを指定してください。"
)

MASTER_FIELDS: list[mapping.TargetField] = [
    ("office_code", "事業所番号", True),
    ("office_name", "事業所名", False),
    ("addition_code", "加算コード", True),
    ("addition_name", "加算名称", True),
    ("category", "区分(体制/個別)", False),
    ("unit", "単位数", False),
    ("valid_from", "適用開始年月(YYYY-MM)", False),
    ("valid_to", "適用終了年月(YYYY-MM)", False),
    ("note", "備考", False),
]


def _normalize_category(value) -> str:
    text = str(value).strip() if pd.notna(value) else ""
    return "体制" if "体制" in text else "個別"


def _read_uploaded(file) -> pd.DataFrame | None:
    name = file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(file, dtype=str)
    xls = pd.ExcelFile(file)
    sheet = xls.sheet_names[0]
    if len(xls.sheet_names) > 1:
        sheet = st.selectbox("シートを選択してください", xls.sheet_names)
    return pd.read_excel(xls, sheet_name=sheet, dtype=str)


st.subheader("1. 加算マスタの取り込み")
uploaded = st.file_uploader("加算マスタファイル(Excel または CSV)", type=["xlsx", "xls", "csv"])

if uploaded is not None:
    df = _read_uploaded(uploaded)
    if df is not None:
        st.write("取り込みデータのプレビュー(先頭5行)")
        st.dataframe(df.head(), use_container_width=True)

        map_result, signature, missing = mapping.render_mapping_ui(
            df, MASTER_FIELDS, file_type="addition_master", conn=conn, form_key="master_map"
        )

        if missing:
            st.warning(f"必須項目が未設定です: {', '.join(missing)}")
        else:
            mapped = mapping.apply_mapping(df, map_result)
            mapped["office_code"] = mapped["office_code"].astype(str).str.strip()
            mapped["addition_code"] = mapped["addition_code"].astype(str).str.strip()
            if "category" in mapped.columns:
                mapped["category"] = mapped["category"].apply(_normalize_category)
            else:
                mapped["category"] = "個別"

            st.write("マッピング適用後のプレビュー")
            st.dataframe(mapped.head(10), use_container_width=True)

            n_offices = mapped["office_code"].nunique()
            st.caption(f"{n_offices}事業所、{len(mapped)}件の加算データを取り込みます。")

            if st.button("この内容でマスタに取り込む", type="primary"):
                db.save_mapping(conn, "addition_master", signature, map_result)
                for office_code, group in mapped.groupby("office_code"):
                    office_name = ""
                    if "office_name" in group.columns:
                        names = group["office_name"].dropna()
                        office_name = names.iloc[0] if not names.empty else ""
                    db.upsert_office(conn, office_code, office_name)
                    rows = group.to_dict("records")
                    db.replace_addition_master(conn, office_code, rows)
                st.success(f"{n_offices}事業所分の加算マスタを取り込みました。")
                st.rerun()

st.divider()
st.subheader("2. 登録済み加算マスタの確認・修正")

offices_df = db.list_offices(conn)
if offices_df.empty:
    st.info("まだ加算マスタが登録されていません。上のフォームから取り込んでください。")
else:
    office_options = ["すべて"] + offices_df["office_code"].tolist()
    selected_office = st.selectbox("事業所で絞り込み", office_options)

    master_df = db.get_addition_master(conn, office_code=None if selected_office == "すべて" else selected_office)
    st.dataframe(master_df, use_container_width=True, hide_index=True)

    with st.expander("区分(体制/個別)をまとめて変更する"):
        st.caption(
            "「体制」に設定した加算は、レセプト突合チェックで未請求だった場合に重要度「高」の警告になります。"
            "夜間支援等体制加算など、毎月固定的に算定される加算を「体制」に設定してください。"
        )
        edit_office = st.selectbox("事業所", offices_df["office_code"].tolist(), key="cat_office")
        office_master = db.get_addition_master(conn, office_code=edit_office)
        if not office_master.empty:
            codes = st.multiselect(
                "加算コード",
                office_master["addition_code"].tolist(),
                format_func=lambda c: f"{c} ({office_master[office_master.addition_code == c]['addition_name'].iloc[0]})",
            )
            new_category = st.radio("区分", ["体制", "個別"], horizontal=True)
            if st.button("区分を変更する") and codes:
                target_ids = office_master[office_master.addition_code.isin(codes)]["id"].tolist()
                for row_id in target_ids:
                    db.update_addition_category(conn, int(row_id), new_category)
                st.success(f"{len(target_ids)}件の区分を「{new_category}」に変更しました。")
                st.rerun()

    with st.expander("加算マスタの行を削除する"):
        del_office = st.selectbox("事業所", offices_df["office_code"].tolist(), key="del_office")
        office_master = db.get_addition_master(conn, office_code=del_office)
        if not office_master.empty:
            del_codes = st.multiselect(
                "削除する加算コード",
                office_master["addition_code"].tolist(),
                key="del_codes",
            )
            if st.button("削除する", type="secondary") and del_codes:
                target_ids = office_master[office_master.addition_code.isin(del_codes)]["id"].tolist()
                for row_id in target_ids:
                    db.delete_addition_master_row(conn, int(row_id))
                st.success(f"{len(target_ids)}件を削除しました。")
                st.rerun()
