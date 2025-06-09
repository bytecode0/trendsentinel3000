from datetime import datetime
import hashlib
import hmac
import time
from flask import Flask, render_template, session
from functools import wraps
from firebase_admin import credentials, firestore, initialize_app
import firebase_admin
from flask import session, request, redirect, url_for, flash
import requests
from bot.jobs import start_scheduler
from bot.btc_bot import DEFAULT_LEVERAGE

app = Flask(__name__)
app.secret_key = "tu_clave_secreta_super_segura"  # Cambia esto a algo secreto y seguro

# Inicializa Firebase
if not firebase_admin._apps:
    cred = credentials.Certificate("trendsentinelbitcoin-firebase-adminsdk-fbsvc-a11c5f2129.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

# Decorador para rutas protegidas
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_email" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/")
def index():
    if "user_email" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        # Verificar password estático
        if password == "TrendSentinel3000":
            # Aquí opcionalmente podrías verificar que el email exista en Firestore
            session["user_email"] = email
            flash("Login exitoso!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Contraseña incorrecta", "danger")

    return render_template("login.html")

@app.route("/dashboard")
@login_required
def dashboard():
    user_email = session["user_email"]

    user_data = get_user_data(user_email)
    if not user_data:
        flash("Usuario no encontrado", "danger")
        return redirect(url_for("login"))
    
    config = get_bot_config(user_email)
    if config:
        print(f"User {user_email} config: {config}")

    default_leverage = config.get("default_leverage", DEFAULT_LEVERAGE) if config else DEFAULT_LEVERAGE
    
    bybit_api_key = user_data["bybit_api_key"]
    bybit_api_secret = user_data["bybit_api_secret"]

    trades_ref = db.collection("users").document(user_email).collection("trades")
    trades = [doc.to_dict() for doc in trades_ref.stream()]

    total_margin = 0
    total_unrealized_pnl = 0

    for trade in trades:
        try:
            symbol = trade.get("symbol", "BTCUSDT")
            entry_price = float(trade.get("entry_price", 0))
            qty = float(trade.get("qty", 0))
            operation_type = trade.get("operation_type", "long").lower()

            if entry_price > 0 and qty > 0:
                current_price = get_current_price(symbol)

                # Margen requerido considerando leverage
                margin_required = (qty * entry_price) / default_leverage
                trade["margin"] = round(margin_required, 2)
                total_margin += margin_required

                # Cálculo del PNL
                if operation_type == "long":
                    pnl_pct = ((current_price - entry_price) / entry_price) * 100
                    pnl_usdt = (current_price - entry_price) * qty
                elif operation_type == "short":
                    pnl_pct = ((entry_price - current_price) / entry_price) * 100
                    pnl_usdt = (entry_price - current_price) * qty
                else:
                    pnl_pct = 0.0
                    pnl_usdt = 0.0

                trade["pnl_pct"] = round(pnl_pct, 2)
                trade["pnl_usdt"] = round(pnl_usdt, 2)
                total_unrealized_pnl += pnl_usdt
            else:
                trade["margin"] = 0
                trade["pnl_pct"] = "-"
                trade["pnl_usdt"] = "-"
        except Exception as e:
            trade["margin"] = 0
            trade["pnl_pct"] = "-"
            trade["pnl_usdt"] = "-"
            print(f"Error calculando margin/pnl para trade: {e}")
    
    usdt_balance = get_usdt_available_balance(bybit_api_key, bybit_api_secret)
    balance_total = usdt_balance + total_margin + total_unrealized_pnl
    equity_history = get_equity_history_with_latest(api_key=bybit_api_key, api_secret=bybit_api_secret, user_email=user_email)
    equity_history.append({
        "timestamp": datetime.utcnow().isoformat(),
        "equity": balance_total
    })
    
    return render_template(
        "dashboard.html",
        balance_total=round(balance_total, 2),
        btc_price = get_current_price("BTCUSDT"),
        balance=round(usdt_balance),
        margin=round(total_margin, 2),
        pnl=round(total_unrealized_pnl, 2),
        user=user_email,
        trades=trades,
        default_leverage=default_leverage,
        equity_history=equity_history
    )

@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("Has cerrado sesión", "info")
    return redirect(url_for("login"))

def get_user_data(user_id):
    user_ref = db.collection("users").document(user_id)
    user_doc = user_ref.get()
    if user_doc.exists:
        return user_doc.to_dict()
    else:
        return None

def get_open_trade_for_user(user_id):
    trades_ref = db.collection("users").document(user_id).collection("trades").where("status", "==", "OPEN")
    open_trades = list(trades_ref.stream())
    
    return open_trades

def get_usdt_available_balance(api_key, api_secret):
    base_url = "https://api-demo.bybit.com"
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    endpoint = "/v5/account/wallet-balance"
    params = f"accountType=UNIFIED"  # O 'CONTRACT' si usás cuenta tipo contrato

    # Cadena que firmar
    to_sign = f"{timestamp}{api_key}{recv_window}{params}"
    signature = hmac.new(
        bytes(api_secret, "utf-8"),
        to_sign.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }

    url = f"{base_url}{endpoint}?{params}"
    response = requests.get(url, headers=headers)

    data = response.json()
    coins = data["result"]["list"][0]["coin"]
    for coin in coins:
        if coin["coin"] == "USDT":
            wallet_balance = float(coin["walletBalance"])
            used_margin = float(coin["totalPositionIM"])
            available_balance = wallet_balance - used_margin
            return available_balance
    return 0.0  # Si no se encuentra USDT

def get_current_price(symbol):
    from pybit.unified_trading import HTTP

    # Crea una sesión con testnet=False si estás usando el entorno real
    session = HTTP(testnet=False)

    # Obtener el precio actual de BTCUSDT en el mercado de perpetuos (linear)
    response = session.get_tickers(
        category="linear",
        symbol=symbol
    )

    # Extraer el precio
    price = float(response["result"]["list"][0]["lastPrice"])
    print(f"Precio actual BTCUSDT: {price}")
    return price

def get_bot_config(user_id):
    config_ref = db.collection("users").document(user_id).collection("config").document("default")
    doc = config_ref.get()
    if doc.exists:
        config_data = doc.to_dict()
        return config_data
    else:
        print(f"No hay configuración para el usuario {user_id}")
        return None
    
def get_equity_history_with_latest(api_key, api_secret, user_email):
    equity_ref = db.collection("users").document(user_email).collection("equity_history")
    docs = equity_ref.stream()

    equity_data = []

    for doc in docs:
        d = doc.to_dict()
        equity_data.append({
            "timestamp": d["timestamp"],
            "equity": d["equity"]
        })

    # Ordenar por timestamp
    equity_data.sort(key=lambda x: x["timestamp"])

    return equity_data

def run_admin_panel():
    app.run(debug=False)

#if __name__ == "__main__":
#    run_admin_panel()