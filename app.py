import os
import requests
from flask import Flask, render_template, request, jsonify
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None

EXCHANGE_CACHE = {"rates": {"KRW": 1360.0, "EUR": 0.92, "JPY": 155.0}, "updated": ""}

# Incoterms 2020 규칙별 셀러 부담 비용 항목 정의
# 1: 셀러 부담, 0: 바이어 부담
INCOTERMS_RULES = {
    # 복합운송 (모든 운송수단)
    "EXW": {"export_clearance": 0, "local_freight": 0, "intl_freight": 0, "insurance": 0, "dest_clearance": 0, "dest_duty": 0, "dest_delivery": 0, "dest_unloading": 0},
    "FCA": {"export_clearance": 1, "local_freight": 1, "intl_freight": 0, "insurance": 0, "dest_clearance": 0, "dest_duty": 0, "dest_delivery": 0, "dest_unloading": 0},
    "CPT": {"export_clearance": 1, "local_freight": 1, "intl_freight": 1, "insurance": 0, "dest_clearance": 0, "dest_duty": 0, "dest_delivery": 0, "dest_unloading": 0},
    "CIP": {"export_clearance": 1, "local_freight": 1, "intl_freight": 1, "insurance": 1, "dest_clearance": 0, "dest_duty": 0, "dest_delivery": 0, "dest_unloading": 0},
    "DAP": {"export_clearance": 1, "local_freight": 1, "intl_freight": 1, "insurance": 1, "dest_clearance": 0, "dest_duty": 0, "dest_delivery": 1, "dest_unloading": 0},
    "DPU": {"export_clearance": 1, "local_freight": 1, "intl_freight": 1, "insurance": 1, "dest_clearance": 0, "dest_duty": 0, "dest_delivery": 1, "dest_unloading": 1},
    "DDP": {"export_clearance": 1, "local_freight": 1, "intl_freight": 1, "insurance": 1, "dest_clearance": 1, "dest_duty": 1, "dest_delivery": 1, "dest_unloading": 0},
    # 해상 및 내수로 운송 전용
    "FAS": {"export_clearance": 1, "local_freight": 1, "intl_freight": 0, "insurance": 0, "dest_clearance": 0, "dest_duty": 0, "dest_delivery": 0, "dest_unloading": 0},
    "FOB": {"export_clearance": 1, "local_freight": 1, "intl_freight": 0, "insurance": 0, "dest_clearance": 0, "dest_duty": 0, "dest_delivery": 0, "dest_unloading": 0},
    "CFR": {"export_clearance": 1, "local_freight": 1, "intl_freight": 1, "insurance": 0, "dest_clearance": 0, "dest_duty": 0, "dest_delivery": 0, "dest_unloading": 0},
    "CIF": {"export_clearance": 1, "local_freight": 1, "intl_freight": 1, "insurance": 1, "dest_clearance": 0, "dest_duty": 0, "dest_delivery": 0, "dest_unloading": 0},
}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/guide")
def guide():
    return render_template("guide.html")

@app.route("/breakdown")
def breakdown():
    return render_template("breakdown.html")

@app.route("/api/live-rates", methods=["GET"])
def get_live_rates():
    global EXCHANGE_CACHE
    try:
        res = requests.get("https://open.er-api.com/v6/latest/USD", timeout=3)
        if res.status_code == 200:
            rates = res.json().get("rates", {})
            EXCHANGE_CACHE["rates"] = {
                "KRW": round(rates.get("KRW", 1360.0), 2),
                "EUR": round(rates.get("EUR", 0.92), 4),
                "JPY": round(rates.get("JPY", 155.0), 2),
            }
            EXCHANGE_CACHE["updated"] = res.json().get("time_last_update_utc", "")[:16]
    except Exception:
        pass
    return jsonify(EXCHANGE_CACHE)

@app.route("/api/calculate", methods=["POST"])
def calculate():
    data = request.json or {}
    term = data.get("incoterm", "FOB").upper()
    trade_mode = data.get("trade_mode", "B2B") # B2B or ECOMMERCE
    currency = data.get("currency", "KRW")
    rate = float(data.get("exchange_rate", 1360.0))

    rule = INCOTERMS_RULES.get(term, INCOTERMS_RULES["FOB"])

    def to_krw(val):
        val = float(val or 0)
        return val * rate if currency == "USD" else val

    # 1. 원가 요소 파싱 (KRW 기준 단가 환산)
    exw_pure = to_krw(data.get("exw_val", 15000))
    pack_cost = to_krw(data.get("pack_val", 1200))
    local_transport = to_krw(data.get("local_trans_val", 2500))  # 내륙운송 + 수출통관(FOB/FCA)
    intl_freight = to_krw(data.get("intl_freight_val", 6500))    # 국제운임(CFR/CIF/CPT/CIP/DAP/DPU/DDP)
    insurance = to_krw(data.get("insurance_val", 800))           # 적하보험(CIF/CIP/D그룹)
    dest_delivery = to_krw(data.get("dest_delivery_val", 3000))  # 도착지 내륙운송(DAP/DPU/DDP)
    dest_unloading = to_krw(data.get("dest_unloading_val", 1500)) # 도착지 양하비용(DPU 전용)

    duty_rate = float(data.get("duty_rate", 8)) / 100.0
    vat_rate = float(data.get("vat_rate", 10)) / 100.0

    # 목표 계약금액(판매단가)
    contract_price = float(data.get("target_price", 35.0))
    contract_rev_krw = contract_price * rate if currency == "USD" else contract_price

    # 2. 선택된 Incoterms 조건에 따라 셀러 원가 누적
    seller_cost_total = exw_pure + pack_cost # 기본 공장도원가(항상 셀러부담)

    if rule["local_freight"]:
        seller_cost_total += local_transport
    if rule["intl_freight"]:
        seller_cost_total += intl_freight
    if rule["insurance"]:
        seller_cost_total += insurance
    if rule["dest_delivery"]:
        seller_cost_total += dest_delivery
    if rule["dest_unloading"]:
        seller_cost_total += dest_unloading

    # 수입 관부가세 (DDP 조건일 때만 셀러 부담)
    tariff = 0
    vat = 0
    if rule["dest_duty"]:
        cif_est = exw_pure + pack_cost + local_transport + intl_freight + insurance
        tariff = cif_est * duty_rate
        vat = (cif_est + tariff) * vat_rate
        seller_cost_total += (tariff + vat)

    # 3. 거래 방식별 부대 수수료
    operating_fees = 0
    if trade_mode == "ECOMMERCE":
        platform_fee_rate = float(data.get("platform_fee_rate", 15.0)) / 100.0
        extra_fee_rate = float(data.get("extra_fee_rate", 7.8)) / 100.0
        operating_fees = contract_rev_krw * (platform_fee_rate + extra_fee_rate)
    else:
        # 일반 B2B 무역 수수료: L/C 결제, 외환 송금 수수료 등 (약 1.5%)
        b2b_banking_fee_rate = 0.015
        operating_fees = contract_rev_krw * b2b_banking_fee_rate

    seller_total_expense = seller_cost_total + operating_fees
    net_profit_krw = contract_rev_krw - seller_total_expense
    margin_rate = (net_profit_krw / contract_rev_krw * 100) if contract_rev_krw > 0 else 0

    # 권장 견적가 산출 (목표 마진 20% 보장 시)
    target_margin_ratio = 0.20
    denom = 1 - (target_margin_ratio + (operating_fees / contract_rev_krw if contract_rev_krw else 0.02))
    rec_quote_usd = (seller_cost_total / denom / rate) if (denom > 0 and rate > 0) else 0

    return jsonify({
        "incoterm": term,
        "trade_mode": trade_mode,
        "currency": currency,
        "rate": rate,
        "gross_rev_krw": round(contract_rev_krw),
        "net_profit_krw": round(net_profit_krw),
        "margin_rate": round(margin_rate, 2),
        "rec_quote_usd": round(rec_quote_usd, 2),
        "seller_cost_total": round(seller_cost_total),
        "operating_fees": round(operating_fees),
        "cost_items": {
            "exw": round(exw_pure + pack_cost),
            "local_trans": round(local_transport) if rule["local_freight"] else 0,
            "intl_freight": round(intl_freight) if rule["intl_freight"] else 0,
            "insurance": round(insurance) if rule["insurance"] else 0,
            "dest_delivery": round(dest_delivery) if rule["dest_delivery"] else 0,
            "dest_unloading": round(dest_unloading) if rule["dest_unloading"] else 0,
            "tariff": round(tariff),
            "vat": round(vat)
        }
    })

@app.route("/api/chat", methods=["POST"])
def chat():
    if not client:
        return jsonify({"reply": "OpenAI API 키가 설정되지 않았습니다. .env 파일을 확인해주세요."}), 500
    data = request.json or {}
    messages = data.get("messages", [])
    
    system_prompt = {
        "role": "system",
        "content": (
            "당신은 ICC 공식 Incoterms 2020 및 글로벌 무역 원가/수출 정산 수석 컨설턴트입니다. "
            "11개 조건(EXW, FCA, CPT, CIP, DAP, DPU, DDP, FAS, FOB, CFR, CIF)의 위험 이전 분기점, "
            "운송수단 적합성(해상/복합), L/C 선하증권(B/L) 발급 실무, 관세 및 마진 산출 팁을 "
            "가독성 높은 번호 목록과 굵은 글씨를 활용하여 명쾌하게 한국어로 조언하십시오."
        )
    }

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[system_prompt] + messages,
            temperature=0.3,
            max_tokens=800
        )
        return jsonify({"reply": res.choices[0].message.content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)