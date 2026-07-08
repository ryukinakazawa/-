"""加算マスタとレセプト請求明細の突合ロジック。

事業所ごとに、
  1. マスタに登録されていない加算コードが請求されていないか(誤請求/マスタ登録漏れの疑い)
  2. マスタに登録されている加算が、今回の請求明細で一件も算定されていないか(算定漏れの疑い)
を検出する。

「体制」区分の加算(夜間支援等体制加算など、事業所の体制に応じて毎月固定的に
算定されるもの)は未請求だと算定漏れの可能性が高いため重要度を高くする。
「個別」区分の加算は利用者の状態に応じて都度算定されるものなので、
未請求でも異常とは限らず参考情報として扱う。
"""
import pandas as pd

from lib import db

ISSUE_COLUMNS = ["事業所番号", "事業所名", "種別", "加算コード", "加算名称", "重要度", "詳細"]

_SEVERITY_ORDER = {"高": 0, "参考": 1}


def _is_active(row: pd.Series, target_month: str) -> bool:
    valid_from = row.get("valid_from")
    valid_to = row.get("valid_to")
    if valid_from and str(valid_from) > target_month:
        return False
    if valid_to and str(valid_to) < target_month:
        return False
    return True


def run_check(conn, target_month: str) -> pd.DataFrame:
    master = db.get_addition_master(conn)
    receipts = db.get_receipts(conn, target_month=target_month)

    if not master.empty:
        master = master[master.apply(_is_active, axis=1, target_month=target_month)]

    office_names: dict[str, str] = {}
    offices_df = db.list_offices(conn)
    for _, r in offices_df.iterrows():
        office_names[r["office_code"]] = r["office_name"]
    if not receipts.empty:
        for _, r in receipts.drop_duplicates("office_code").iterrows():
            office_names.setdefault(r["office_code"], r.get("office_name") or "")
    if not master.empty:
        for _, r in master.drop_duplicates("office_code").iterrows():
            office_names.setdefault(r["office_code"], "")

    offices = set(master["office_code"]) if not master.empty else set()
    offices |= set(receipts["office_code"]) if not receipts.empty else set()

    issues = []
    for office_code in sorted(offices):
        office_master = master[master.office_code == office_code] if not master.empty else master
        office_receipts = receipts[receipts.office_code == office_code] if not receipts.empty else receipts
        office_name = office_names.get(office_code, "")

        master_codes = set(office_master["addition_code"]) if not office_master.empty else set()
        receipt_codes = set(office_receipts["addition_code"]) if not office_receipts.empty else set()

        for code in sorted(receipt_codes - master_codes):
            sample = office_receipts[office_receipts.addition_code == code].iloc[0]
            issues.append({
                "事業所番号": office_code,
                "事業所名": office_name,
                "種別": "未登録の加算を請求",
                "加算コード": code,
                "加算名称": sample.get("addition_name") or "",
                "重要度": "高",
                "詳細": "この加算コードは加算マスタに登録されていません。誤請求、またはマスタの登録漏れの可能性があります。",
            })

        for code in sorted(master_codes - receipt_codes):
            sample = office_master[office_master.addition_code == code].iloc[0]
            category = sample.get("category") or "個別"
            if category == "体制":
                severity = "高"
                detail = "体制系の加算として登録されていますが、今回の請求明細に算定がありません。算定漏れの可能性があります。"
            else:
                severity = "参考"
                detail = "取得している加算ですが、今回の請求明細には算定がありません(利用者の状態により対象者がいない場合は問題ありません)。"
            issues.append({
                "事業所番号": office_code,
                "事業所名": office_name,
                "種別": "取得加算の未請求",
                "加算コード": code,
                "加算名称": sample.get("addition_name") or "",
                "重要度": severity,
                "詳細": detail,
            })

    if not issues:
        return pd.DataFrame(columns=ISSUE_COLUMNS)

    result = pd.DataFrame(issues)
    result["_order"] = result["重要度"].map(_SEVERITY_ORDER).fillna(2)
    result = result.sort_values(["_order", "事業所番号", "加算コード"]).drop(columns="_order")
    return result.reset_index(drop=True)
