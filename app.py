import streamlit as st

from lib import db

st.set_page_config(page_title="加算・レセプト突合チェック", page_icon="📋", layout="wide")

conn = db.get_conn()
db.init_db(conn)

st.title("加算・レセプト突合チェックアプリ")
st.write(
    "共同生活援助(グループホーム)・日中サービス支援型共同生活援助を対象に、"
    "各事業所が取得している加算と、国保連への請求明細(レセプト)の内容が"
    "合致しているかを毎月確認するための社内ツールです。"
)

offices = db.list_offices(conn)
master = db.get_addition_master(conn)
months = db.list_target_months(conn)

col1, col2, col3 = st.columns(3)
col1.metric("登録事業所数", len(offices))
col2.metric("加算マスタ登録件数", len(master))
col3.metric("請求明細を取り込み済みの対象年月", len(months))

st.divider()
st.subheader("使い方")
st.markdown(
    """
    1. **「加算マスタ管理」ページ** で、各事業所が取得している加算の一覧(Excel)を取り込みます。
       - 初回のみ、どの列が「事業所番号」「加算コード」などに対応するかを画面上で指定します。
       - 一度指定した列構成は記憶され、次回以降は自動的に適用されます。
    2. **「レセプト突合チェック」ページ** で、国保連の請求ソフトから出力した当月の請求明細CSVを取り込みます。
       - 同様に列を指定すると、加算マスタとの突合結果(未登録の加算を請求していないか、
         取得している加算の算定漏れがないか)が一覧表示されます。
       - 結果はCSVでダウンロードできます。
    """
)

if months:
    st.caption(f"取り込み済みの対象年月: {', '.join(months)}")
