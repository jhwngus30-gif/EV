import os
import requests
from flask import Flask, render_template, request, jsonify
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None

# 캐시용 환율 데이터
EXCHANGE_CACHE = {"rates": {"KRW": 1360.0, "EUR": 0.92, "JPY": 155.0, "CNY": 7.25}, "updated": ""}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/guide")
def guide():
    return render_template("guide.html")

@app.route("/breakdown")
def breakdown():
    return render_template("breakdown.html")

# 1. 실시간 환율 조회 API
@app.route("/api/live-rates", methods=["GET"])
def get_live_rates():
    global EXCHANGE_CACHE
    try:
        # 인증키 없이 무료 제공되는 글로벌 오픈 환율 API
        res = requests.get("https://open.er-api.com/v6/latest/USD", timeout=4)
        if res.status_code == 200:
            data = res.json()
            rates = data.get("rates", {})
            EXCHANGE_CACHE["rates"] = {
                "KRW": round(rates.get("KRW", 1360.0), 2),
                "EUR": round(rates.get("EUR", 0.92), 4),
                "JPY": round(rates.get("JPY", 155.0), 2),
                "CNY": round(rates.get("CNY", 7.25), 2),
            }
            EXCHANGE_CACHE["updated"] = data.get("time_last_update_utc", "")[:16]
    except Exception as e:
        pass # 실패 시 캐시된 기본값 사용
    return jsonify(EXCHANGE_CACHE)

# 2. 고도화된 무역 마진 정산 엔진
@app.route("/api/calculate", methods=["POST"])
def calculate():
    data = request.json or {}

    # 기본 파라미터 수신
    exw_krw = float(data.get("exw_krw", 15000))                 # EXW 제조원가
    packaging_cost = float(data.get("packaging_cost", 1200))     # 수출 포장/바코드/라벨링
    local_transport = float(data.get("local_transport", 2500))   # 수출 통관+내륙운송+공항/항만하역
    intl_freight = float(data.get("intl_freight", 6500))         # 국제운임 (해상/항공)
    insurance_fee = float(data.get("insurance_fee", 800))        # 적하보험료
    duty_rate = float(data.get("duty_rate", 8)) / 100.0          # 수입 관세율
    vat_rate = float(data.get("vat_rate", 10)) / 100.0           # 현지 소비세/VAT
    platform_fee_rate = float(data.get("platform_fee_rate", 15)) / 100.0 # 쇼피/아마존 수수료
    pg_fee_rate = float(data.get("pg_fee_rate", 3.0)) / 100.0    # 결제 수수료
    fx_spread_rate = float(data.get("fx_spread_rate", 1.8)) / 100.0 # 해외 송금/환전 수수료
    return_loss_rate = float(data.get("return_loss_rate", 3.0)) / 100.0 # 반품/분실 손실충당
    monthly_fixed_cost = float(data.get("monthly_fixed_cost", 1500000)) # 월 고정 운영비

    target_price_usd = float(data.get("target_price_usd", 39.0)) # 판매가 (USD)
    exchange_rate = float(data.get("exchange_rate", 1360.0))     # 적용 환율

    # 인코텀즈 단계별 원가 누적
    base_exw = exw_krw + packaging_cost
    fob_cost = base_exw + local_transport
    cif_cost = fob_cost + intl_freight + insurance_fee

    # DDP 관부가세
    tariff = cif_cost * duty_rate
    duty_paid = cif_cost + tariff
    vat = duty_paid * vat_rate
    ddp_logistics_cost = duty_paid + vat

    # 플랫폼 정산 수수료 및 부가비용
    gross_revenue_krw = target_price_usd * exchange_rate
    platform_fee = gross_revenue_krw * platform_fee_rate
    pg_fee = gross_revenue_krw * pg_fee_rate
    fx_fee = gross_revenue_krw * fx_spread_rate
    return_loss = gross_revenue_krw * return_loss_rate

    total_operating_deductions = platform_fee + pg_fee + fx_fee + return_loss
    total_ddp_cost = ddp_logistics_cost + total_operating_deductions
    net_profit_krw = gross_revenue_krw - total_ddp_cost
    margin_rate = (net_profit_krw / gross_revenue_krw * 100) if gross_revenue_krw > 0 else 0

    # 목표 마진 25% 달성을 위한 역산 권장 판매가 (USD)
    # TargetRev * (1 - OpDeductionsRatio - MarginRatio) = ddp_logistics_cost
    denom = 1 - (platform_fee_rate + pg_fee_rate + fx_spread_rate + return_loss_rate + 0.25)
    rec_price_usd = (ddp_logistics_cost / denom / exchange_rate) if denom > 0 else 0

    # 손익분기점(BEP) 월 판매 수량
    bep_units = int(monthly_fixed_cost / net_profit_krw) if net_profit_krw > 0 else "달성불가"

    return jsonify({
        "gross_revenue_krw": round(gross_revenue_krw),
        "net_profit_krw": round(net_profit_krw),
        "margin_rate": round(margin_rate, 2),
        "rec_price_usd": round(rec_price_usd, 2),
        "bep_units": bep_units,
        "costs": {
            "exw": round(base_exw),
            "fob": round(fob_cost),
            "cif": round(cif_cost),
            "ddp_logistics": round(ddp_logistics_cost),
            "tariff": round(tariff),
            "vat": round(vat),
            "platform_fee": round(platform_fee),
            "pg_fee": round(pg_fee),
            "fx_fee": round(fx_fee),
            "return_loss": round(return_loss)
        },
        "terms_profit": {
            "exw": round(gross_revenue_krw - base_exw - total_operating_deductions),
            "fob": round(gross_revenue_krw - fob_cost - total_operating_deductions),
            "cif": round(gross_revenue_krw - cif_cost - total_operating_deductions),
            "ddp": round(net_profit_krw)
        }
    })

@app.route("/api/chat", methods=["POST"])
def chat():
    if not client:
        return jsonify({"reply": "OpenAI API 키가 설정되지 않았습니다. .env 환경변수를 확인해주세요."}), 500
    data = request.json or {}
    messages = data.get("messages", [])
    system_prompt = {
        "role": "system",
        "content": (
            "당신은 글로벌 무역 및 크로스보더 이커머스 전문 경영 컨설턴트입니다. "
            "Incoterms 2020 규칙, HS코드 관세, 플랫폼 마진 전략, 환율 헷징, 물류비 절감 팁을 신뢰도 높은 어조로 조언하세요."
        )
    }
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[system_prompt] + messages,
            temperature=0.3,
            max_tokens=600
        )
        return jsonify({"reply": res.choices[0].message.content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)