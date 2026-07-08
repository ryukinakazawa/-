import pandas as pd
import streamlit as st

from lib import db, mapping, matching

st.set_page_config(page_title="レセプト突合チェック", page_icon="✅", layout="wide")

conn = db.get_conn()
db.init_db(conn)

st.title("レセプト突合チェック")
st.write(
    "国保連等の請求ソフトから出力した当月の請求明細CSVを取り込み、"
    "加算マスタ(取得している加算)との突合結果を確認します。"
)

RECEIPT_FIELDS: list[mapping.TargetField] = [
    ("office_code", "事業所番号", True),
    ("office_name", "事業所名", False),
    ("user_code", "利用者番号(受給者番号)", False),
    ("user_name", "利用者名", False),
    ("addition_code", "加算コード(サービスコード)", True),
    ("addition_name", "加算名称(サービス内容略称等)", False),
    ("unit", "単位数", False),
    ("count", "回数・日数", False),
    ("target_month", "対象年月(CSVに列がある場合のみ)", False),
]


def _read_uploaded(file) -> pd.DataFrame | None:
    name = file.name.lower()
    if name.endswith(".csv"):
        for enc in ("utf-8-sig", "cp932", "shift_jis"):
            try:
                file.seek(0)
                return pd.read_csv(file, dtype=str, encoding=enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        file.seek(0)
        return pd.read_csv(file, dtype=str)
    xls = pd.ExcelFile(file)
    sheet = xls.sheet_names[0]
    if len(xls.sheet_names) > 1:
        sheet = st.selectbox("シートを選択してください", xls.sheet_names)
    return pd.read_excel(xls, sheet_name=sheet, dtype=str)


st.subheader("1. 請求明細の取り込み")

target_month = st.text_input("対象年月(例: 2026-06)", placeholder="YYYY-MM")
uploaded = st.file_uploader("請求明細ファイル(CSV または Excel)", type=["csv", "xlsx", "xls"])

if uploaded is not None:
    df = _read_uploaded(uploaded)
    if df is not None:
        st.write("取り込みデータのプレビュー(先頭5行)")
        st.dataframe(df.head(), use_container_width=True)

        map_result, signature, missing = mapping.render_mapping_ui(
            df, RECEIPT_FIELDS, file_type="receipt", conn=conn, form_key="receipt_map"
        )

        if missing:
            st.warning(f"必須項目が未設定です: {', '.join(missing)}")
        elif not target_month.strip() and "target_month" not in map_result:
            st.warning("対象年月を入力するか、対象年月に対応する列をマッピングしてください。")
        else:
            mapped = mapping.apply_mapping(df, map_result)
            mapped["office_code"] = mapped["office_code"].astype(str).str.strip()
            mapped["addition_code"] = mapped["addition_code"].astype(str).str.strip()
            if "target_month" not in mapped.columns:
                mapped["target_month"] = target_month.strip()
            else:
                mapped["target_month"] = mapped["target_month"].fillna(target_month.strip())
                mapped.loc[mapped["target_month"] == "", "target_month"] = target_month.strip()

            st.write("マッピング適用後のプレビュー")
            st.dataframe(mapped.head(10), use_container_width=True)

            months_in_file = sorted(mapped["target_month"].dropna().unique().tolist())
            st.caption(f"対象年月: {', '.join(months_in_file) if months_in_file else '(未設定)'} / {len(mapped)}件")

            replace_existing = st.checkbox(
                "同じ対象年月の既存データを削除してから取り込む(再取り込み時の重複防止・推奨)",
                value=True,
            )

            if st.button("この内容で請求明細を取り込む", type="primary"):
                db.save_mapping(conn, "receipt", signature, map_result)
                if replace_existing:
                    for m in months_in_file:
                        db.delete_receipts_for_month(conn, m)
                batch_id = db.create_import_batch(
                    conn, "receipt", uploaded.name, target_month.strip() or (months_in_file[0] if months_in_file else "")
                )
                db.save_receipts(conn, batch_id, mapped.to_dict("records"))
                for office_code, group in mapped.groupby("office_code"):
                    office_name = ""
                    if "office_name" in group.columns:
                        names = group["office_name"].dropna()
                        office_name = names.iloc[0] if not names.empty else ""
                    db.upsert_office(conn, office_code, office_name)
                st.success(f"{len(mapped)}件の請求明細を取り込みました。")
                st.rerun()

st.divider()
st.subheader("2. 突合チェック結果")

months = db.list_target_months(conn)
if not months:
    st.info("まだ請求明細が取り込まれていません。上のフォームから取り込んでください。")
else:
    check_month = st.selectbox("チェックする対象年月", months)
    if st.button("突合チェックを実行", type="primary"):
        st.session_state["check_result"] = matching.run_check(conn, check_month)
        st.session_state["check_month"] = check_month

    result = st.session_state.get("check_result")
    if result is not None and st.session_state.get("check_month") == check_month:
        if result.empty:
            st.success("不一致は検出されませんでした。")
        else:
            n_high = (result["重要度"] == "高").sum()
            n_ref = (result["重要度"] == "参考").sum()
            c1, c2 = st.columns(2)
            c1.metric("重要度「高」の件数", int(n_high))
            c2.metric("重要度「参考」の件数", int(n_ref))

            severities = st.multiselect("重要度で絞り込み", ["高", "参考"], default=["高", "参考"])
            offices = st.multiselect("事業所で絞り込み", sorted(result["事業所番号"].unique().tolist()))

            filtered = result[result["重要度"].isin(severities)]
            if offices:
                filtered = filtered[filtered["事業所番号"].isin(offices)]

            st.dataframe(filtered, use_container_width=True, hide_index=True)

            csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "この結果をCSVでダウンロード",
                data=csv_bytes,
                file_name=f"突合チェック結果_{check_month}.csv",
                mime="text/csv",
            )
