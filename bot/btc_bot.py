
from firebase_admin import credentials, firestore
from backtesting.lib import crossover
import pandas as pd
import requests
from datetime import datetime
import ta
import firebase_admin
import time
import hmac
import hashlib
import requests
import json 

# Parámetros de la estrategia
FAST = 10
SLOW = 90
ADX_PERIOD = 20
ADX_THRESHOLD = 12
RANGE_LOOKBACK = 6
NARROW_RANGE_THRESHOLD = 0.04  # 4%
SL_BUFFER = 0.
DEFAULT_LEVERAGE = 10 # Leverage por defecto

# Tus claves API demo
API_KEY = "IzN46jpMWJFVDYNMeP"
API_SECRET = "g5aZ0qHaD8zK3K4ogCwXOg7Rl8QofQimCt7f"
BASE_URL = "https://api-demo.bybit.com"

db = None

def get_usdt_available_balance():
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    endpoint = "/v5/account/wallet-balance"
    params = f"accountType=UNIFIED"  # O 'CONTRACT' si usás cuenta tipo contrato

    # Cadena que firmar
    to_sign = f"{timestamp}{API_KEY}{recv_window}{params}"
    signature = hmac.new(
        bytes(API_SECRET, "utf-8"),
        to_sign.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }

    url = f"{BASE_URL}{endpoint}?{params}"
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

def get_account_equity():
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    endpoint = "/v5/account/wallet-balance"
    params = "accountType=UNIFIED"  # Cambia a 'CONTRACT' si aplica

    to_sign = f"{timestamp}{API_KEY}{recv_window}{params}"
    signature = hmac.new(
        bytes(API_SECRET, "utf-8"),
        to_sign.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }

    url = f"{BASE_URL}{endpoint}?{params}"
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise Exception(f"API error: {response.text}")

    data = response.json()
    coins = data["result"]["list"][0]["coin"]
    for coin in coins:
        if coin["coin"] == "USDT":
            equity = float(coin["equity"])
            return equity
    return 0.0

def generate_signature(api_key, api_secret, timestamp, recv_window, body_str):
    to_sign = f"{timestamp}{api_key}{recv_window}{body_str}"
    return hmac.new(api_secret.encode(), to_sign.encode(), hashlib.sha256).hexdigest()

def set_leverage(symbol: str, leverage: int):
    """
    Establece el leverage antes de enviar la orden.
    """
    url = f"{BASE_URL}/v5/position/set-leverage"
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    body = {
        "category": "linear",
        "symbol": symbol,
        "buyLeverage": str(leverage),
        "sellLeverage": str(leverage)
    }
    body_str = json.dumps(body, separators=(',', ':'))
    signature = generate_signature(API_KEY, API_SECRET, timestamp, recv_window, body_str)

    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }

    response = requests.post(url, headers=headers, data=body_str)
    print("Set Leverage Response:", response.status_code, response.text)


def place_order(symbol: str, side: str, qty: float, order_type: str = "Market", price: float = None):
    """
    Envía una orden LONG o SHORT con leverage 10x.
    :param symbol: e.g. 'BTCUSDT'
    :param side: 'Buy' para LONG, 'Sell' para SHORT
    :param qty: cantidad a operar
    :param order_type: 'Market' o 'Limit'
    :param price: requerido si es Limit
    """
    set_leverage(symbol, DEFAULT_LEVERAGE)

    url = f"{BASE_URL}/v5/order/create"
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"

    body = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": order_type,
        "qty": str(qty),
        "timeInForce": "GTC" if order_type == "Limit" else "IOC"
    }
    print("Placing order with body:", body)
    if order_type == "Limit":
        if not price:
            raise ValueError("Debes especificar el precio para órdenes Limit")
        body["price"] = str(price)

    body_str = json.dumps(body, separators=(',', ':'))
    signature = generate_signature(API_KEY, API_SECRET, timestamp, recv_window, body_str)

    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }

    response = requests.post(url, headers=headers, data=body_str)
    print("Order Response:", response.status_code, response.text)
    return response.json()

def get_klines(symbol="BTCUSDT", interval="4h", limit=200):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    response = requests.get(url)
    data = response.json()
    df = pd.DataFrame(data, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    return df

def check_crossover(fast_ema, slow_ema, adx_period, adx_threshold, range_lookback, narrow_range_threshold, sl_buffer):
    df = get_klines()

    # Convertir tipos
    for col in ["high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["high", "low", "close"], inplace=True)

    # EMAs
    df["fast_ema"] = df["close"].ewm(span=fast_ema, adjust=False).mean()
    df["slow_ema"] = df["close"].ewm(span=slow_ema, adjust=False).mean()

    # ADX
    df["adx"] = ta.trend.ADXIndicator(high=df["high"], low=df["low"], close=df["close"], window=adx_period).adx()

    # Filtramos por última fila válida
    last_idx = -1
    last_row = df.iloc[last_idx]

    # Filtro de ADX
    if last_row["adx"] < adx_threshold:
        return None

    # Filtro de rango estrecho
    recent_range = df["high"].iloc[-range_lookback:].max() - df["low"].iloc[-range_lookback:].min()
    if recent_range < last_row["close"] * narrow_range_threshold:
        return None

    # Crossover (última vela)
    if crossover(df["fast_ema"], df["slow_ema"])[last_idx]:
        stop_loss = last_row["slow_ema"] * (1 - sl_buffer)
        return {
            "signal": "LONG",
            "entry_price": last_row["close"],
            "stop_loss": stop_loss,
            "close_time": datetime.utcfromtimestamp(last_row["timestamp"] / 1000.0),
            "fast_ema": last_row["fast_ema"],
            "slow_ema": last_row["slow_ema"],
            "adx": last_row["adx"]
        }

    elif crossover(df["slow_ema"], df["fast_ema"])[last_idx]:
        stop_loss = last_row["slow_ema"] * (1 + sl_buffer)
        return {
            "signal": "SHORT",
            "entry_price": last_row["close"],
            "stop_loss": stop_loss,
            "close_time": datetime.utcfromtimestamp(last_row["timestamp"] / 1000.0),
            "fast_ema": last_row["fast_ema"],
            "slow_ema": last_row["slow_ema"],
            "adx": last_row["adx"]
        }

    return None

from datetime import datetime

def check_crossover_mock(signal_type="LONG"):
    if signal_type == "LONG":
        prev_row = {
            "fast_ema": 106195,
            "slow_ema": 106100
        }
        last_row = {
            "entry_price": 106300,
            "fast_ema": 106195,
            "slow_ema": 106100,
            "close_time": 1685629200000
        }
    elif signal_type == "SHORT":
        prev_row = {
            "fast_ema": 109500,
            "slow_ema": 109000
        }
        last_row = {
            "entry_price": 108000,
            "fast_ema": 108000,
            "slow_ema": 108500,
            "close_time": 1685629200000
        }
    else:
        return None

    close_time_dt = datetime.utcfromtimestamp(last_row["close_time"] / 1000.0)
    # Simular stop loss
    stop_loss = last_row["slow_ema"] * (1 - 0.02) if signal_type == "LONG" else last_row["slow_ema"] * (1 + 0.02)
    return {
        "signal": signal_type,
        "stop_loss": stop_loss,
        "entry_price": last_row["entry_price"],
        "close_time": close_time_dt,
        "fast_ema": last_row["fast_ema"],
        "slow_ema": last_row["slow_ema"],
        "adx": 15  # Valor simulado de ADX
    }
import math

def adjust_qty(qty, step=0.001):
    # Ajusta qty al múltiplo más bajo permitido
    adjusted_qty = math.floor(qty / step) * step
    # Formatea con 3 decimales y lo convierte a string
    return f"{adjusted_qty:.3f}"
    
def open_trade(user_id, signal_data):
    trades_ref = db.collection("users").document(user_id).collection("trades")
    trades_ref.add({
        "symbol": "BTCUSDT",
        "strategy": "TrendSentinel3000",
        "operation_type": signal_data["signal"],
        "entry_price": signal_data["entry_price"],
        "stop_loss": signal_data["stop_loss"],
        "adx": signal_data["adx"],
        "entry_time": signal_data["close_time"].isoformat(),
        "qty": adjust_qty(signal_data["qty"]),
        "exit_price": None,
        "exit_time": None,
        "status": "OPEN",
        "pnl_pct": None,
        "notes": "Auto-generated by crossover bot with ADX filter"
    })
    place_order(
        symbol="BTCUSDT",
        side="Buy" if signal_data["signal"] == "LONG" else "Sell",
        qty=adjust_qty(signal_data["qty"]),  # Cantidad a operar
        order_type="Market"
    )

def record_equity(user_id, equity, btc_price):
    current = datetime.utcnow()
    equity_history_ref = db.collection("users").document(user_id).collection("equity_history")

    data = {
        "timestamp": current.isoformat(),
        "equity": equity,
        "btc_price": btc_price,
    }

    # Usamos el timestamp como ID del documento
    doc_id = current.strftime("%Y%m%d%H%M")
    equity_history_ref.document(doc_id).set(data)

def close_open_trade(user_id, current_price):
    trades_ref = db.collection("users").document(user_id).collection("trades").where("status", "==", "OPEN")
    open_trades = trades_ref.stream()

    for doc in open_trades:
        trade = doc.to_dict()
        close_side = "Sell" if trade["operation_type"] == "LONG" else "Buy"
        place_order(
            symbol="BTCUSDT",
            side=close_side,
            qty=trade["qty"],
            order_type="Market"
        )
        pnl_pct = ((current_price - trade["entry_price"]) / trade["entry_price"]) * 100
        db.collection("users").document(user_id).collection("trades").document(doc.id).update({
            "exit_price": current_price,
            "exit_time": datetime.utcnow().isoformat(),
            "status": "CLOSED",
            "pnl_pct": pnl_pct
        })

def send_signal(signal_data):
    # Guardar en Firebase
    now = datetime.utcnow()
    db.collection("signals").add({
        "timestamp": now.isoformat(),
        "signal": signal_data["signal"],
        "symbol": "BTCUSDT",
        "interval": "4h",
        "entry_price": signal_data["entry_price"],
        "stop_loss": signal_data["stop_loss"],
        "adx": signal_data["adx"],
        "close_time": signal_data["close_time"].isoformat(),
        "fast_ema": signal_data["fast_ema"],
        "slow_ema": signal_data["slow_ema"],
        "strategy": "TrendSentinel3000"
    })

def calculate_btc_quantity(usdt_balance, btc_price, risk_percentage=0.70, leverage=10):
    usdt_to_use = usdt_balance * risk_percentage * leverage
    return round(usdt_to_use / btc_price, 6)

def get_bot_config(user_id):
    config_ref = db.collection("users").document(user_id).collection("config").document("default")
    doc = config_ref.get()
    if doc.exists:
        config_data = doc.to_dict()
        return config_data
    else:
        print(f"No hay configuración para el usuario {user_id}")
        return None
    
def get_user(user_id):
    user_ref = db.collection("users").document(user_id)
    doc = user_ref.get()
    if doc.exists:
        user_data = doc.to_dict()
        return user_data
    else:
        print(f"Usuario {user_id} no encontrado.")
        return None

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

# Ejecutar el bot una vez cada 4 horas
def trading_job():
    init_firestore()

    users = db.collection("users").stream()
    for user in users:
        user_data = user.to_dict()
        user_id = user.id
        print(f"Processing user {user_id} with email {user_data.get('email', 'N/A')}")
        
        config = get_bot_config(user_id)
        if config:
            print(f"User {user_id} config: {config}")
        
        fast_ema = config.get("fast_ema", FAST) if config else FAST
        slow_ema = config.get("slow_ema", SLOW) if config else SLOW
        adx_period = config.get("adx_period", ADX_PERIOD) if config else ADX_PERIOD
        adx_threshold = config.get("adx_threshold", ADX_THRESHOLD) if config else ADX_THRESHOLD
        range_lookback = config.get("range_lookback", RANGE_LOOKBACK) if config else RANGE_LOOKBACK
        narrow_range_threshold = config.get("narrow_range_threshold", NARROW_RANGE_THRESHOLD) if config else NARROW_RANGE_THRESHOLD
        sl_buffer = config.get("sl_buffer", SL_BUFFER) if config else SL_BUFFER
        default_leverage = config.get("default_leverage", DEFAULT_LEVERAGE) if config else DEFAULT_LEVERAGE
        
        #signal = check_crossover_mock("SHORT")  # o "LONG" según el caso de prueba
        signal = check_crossover(fast_ema, slow_ema, adx_period, adx_threshold, range_lookback, narrow_range_threshold, sl_buffer) # Llamada real a la API de Binance 
        print("check_crossover returned:", signal)

        usdt_balance = get_usdt_available_balance()
        print(f"Available USDT balance: {usdt_balance}")

        current_price = get_current_price("BTCUSDT")
        equity = get_account_equity()
        record_equity(user_id, equity, current_price)
        
        if signal:
            print(f"[{datetime.utcnow()}] Signal: {signal} entry price {signal['entry_price']}")
            signal["qty"] = calculate_btc_quantity(usdt_balance, signal["entry_price"], default_leverage)

            # Buscar si ya hay una operación abierta
            trades_ref = db.collection("users").document(user_id).collection("trades").where("status", "==", "OPEN")
            open_trades = list(trades_ref.stream())
            
            if open_trades:
                doc = open_trades[0]
                trade = doc.to_dict()
                open_signal = trade["operation_type"]

                if open_signal == signal["signal"]:
                    print(f"Ignoring signal: already in an open {open_signal} trade.")
                    return

                close_open_trade(user_id, signal["entry_price"])
                print(f"Closed {open_signal} trade due to opposite signal.")

            # Abrir nueva operación
            open_trade(user_id, signal)
            print(f"Opened new {signal['signal']} trade at {signal['entry_price']}")

            # Guardar la señal
            send_signal(signal)

        else:
            print(f"[{datetime.utcnow()}] No signal detected.")

def init_firestore():
    global db
    if not firebase_admin._apps:
        cred = credentials.Certificate("trendsentinel3000/trendsentinel3000/trendsentinel3000-firebase-adminsdk.json")
        firebase_admin.initialize_app(cred)
    db = firestore.client()