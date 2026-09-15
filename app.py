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
            data = res.json()
            rates = data.get("rates", {})
            EXCHANGE_CACHE["rates"] = {
                "KRW": round(rates.get("KRW", 1360.0), 2),
                "EUR": round(rates.get("EUR", 0.92), 4),
                "JPY": round(rates.get("JPY", 155.0), 2),
            }
            EXCHANGE_CACHE["updated"] = data.get("time_last_update_utc", "")[:16]
    except Exception:
        pass
    return jsonify(EXCHANGE_CACHE)

@app.route("/api/calculate", methods=["POST"])
def calculate():
    data = request.json or {}
    currency = data.get("currency", "KRW") # "KRW" or "USD"
    rate = float(data.get("exchange_rate", 1360.0))

    def to_krw(val):
        val = float(val or 0)
        return val * rate if currency == "USD" else val

    exw_krw = to_krw(data.get("exw_val", 15000))
    pack_krw = to_krw(data.get("pack_val", 1200))
    local_trans_krw = to_krw(data.get("local_trans_val", 2500))
    intl_freight_krw = to_krw(data.get("intl_freight_val", 6500))
    insurance_krw = to_krw(data.get("insurance_val", 800))
    
    duty_rate = float(data.get("duty_rate", 8)) / 100.0
    vat_rate = float(data.get("vat_rate", 10)) / 100.0
    platform_fee_rate = float(data.get("platform_fee_rate", 15)) / 100.0
    pg_fee_rate = float(data.get("pg_fee_rate", 3.0)) / 100.0
    fx_spread_rate = float(data.get("fx_spread_rate", 1.8)) / 100.0
    return_loss_rate = float(data.get("return_loss_rate", 3.0)) / 100.0

    target_price = float(data.get("target_price", 39.0))
    gross_rev_krw = target_price * rate if currency == "USD" else target_price

    # 인코텀즈 단계별 원가
    exw_total = exw_krw + pack_krw
    fob_total = exw_total + local_trans_krw
    cif_total = fob_total + intl_freight_krw + insurance_krw

    tariff = cif_total * duty_rate
    duty_paid = cif_total + tariff
    vat = duty_paid * vat_rate
    ddp_logistics = duty_paid + vat

    # 플랫폼 및 기타 운영 공제
    platform_fee = gross_rev_krw * platform_fee_rate
    pg_fee = gross_rev_krw * pg_fee_rate
    fx_fee = gross_rev_krw * fx_spread_rate
    return_loss = gross_rev_krw * return_loss_rate
    total_deductions = platform_fee + pg_fee + fx_fee + return_loss

    net_profit_krw = gross_rev_krw - (ddp_logistics + total_deductions)
    margin_rate = (net_profit_krw / gross_rev_krw * 100) if gross_rev_krw > 0 else 0

    denom = 1 - (platform_fee_rate + pg_fee_rate + fx_spread_rate + return_loss_rate + 0.25)
    rec_price_usd = (ddp_logistics / denom / rate) if (denom > 0 and rate > 0) else 0

    return jsonify({
        "currency": currency,
        "rate": rate,
        "gross_rev_krw": round(gross_rev_krw),
        "net_profit_krw": round(net_profit_krw),
        "margin_rate": round(margin_rate, 2),
        "rec_price_usd": round(rec_price_usd, 2),
        "costs": {
            "exw": round(exw_total),
            "fob": round(fob_total),
            "cif": round(cif_total),
            "ddp_logistics": round(ddp_logistics),
            "tariff": round(tariff),
            "vat": round(vat),
            "operating_fees": round(total_deductions)
        },
        "profits": {
            "exw": round(gross_rev_krw - exw_total - total_deductions),
            "fob": round(gross_rev_krw - fob_total - total_deductions),
            "cif": round(gross_rev_krw - cif_total - total_deductions),
            "ddp": round(net_profit_krw)
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
            "당신은 글로벌 무역 및 크로스보더 이커머스 전문 경영 컨설턴트입니다. "
            "이전 대화 맥락을 충분히 파악하며 대답하세요. "
            "가독성을 위해 핵심 요점을 1, 2, 3 번호 매기기와 굵은 글씨를 활용해 깔끔하고 명확하게 한국어로 제시하십시오."
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