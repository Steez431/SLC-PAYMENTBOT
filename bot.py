#!/usr/bin/env python3
"""
SLC Trench Scanner helper (compact, single-file)
- Hardcoded WALLET (your provided address)
- Polls Solscan every 30s for last 50 txs
- Incoming >=0.15 SOL with memo "SLC30" grants access
- Users map their wallet by DMing: /start <SOL_WALLET>
- Stores users in slc_users.json (no DB)
- Daily midnight sweep: expire users after 30 days unless re-paid
- Whitelist prevents kicking
- /myjoin returns join and expiry
- Uses env var SLC_DATA_FILE (e.g., /data/slc_users.json) for persistent storage
"""
import requests, time, json, os, threading
from datetime import datetime, timedelta

# -------- CONFIG --------
WALLET = "GFxQeqQBhgu4yLYLf7BFUBkRkhbfTnkAfwsrN9TEaTZv"

# Prefer environment variables; fall back to literals if you want to hardcode.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "") or ""
SLC_CHAT_ID = os.getenv("SLC_CHAT_ID", "") or ""   # e.g. -1001234567890

# Persistent data path (recommended: set SLC_DATA_FILE=/data/slc_users.json on Render)
DATA_FILE = os.environ.get("SLC_DATA_FILE", "slc_users.json")
os.makedirs(os.path.dirname(DATA_FILE) or ".", exist_ok=True)
# One-time migration if an old local file exists and disk is empty:
legacy = "slc_users.json"
if DATA_FILE != legacy and os.path.exists(legacy) and not os.path.exists(DATA_FILE):
    try:
        with open(legacy, "rb") as src, open(DATA_FILE, "wb") as dst:
            dst.write(src.read())
    except Exception as e:
        print("Migration warning:", e)
# ------------------------

SOLSCAN_BASE = "https://public-api.solscan.io"
TELE_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MIN_LAMPORTS = int(0.15 * 1_000_000_000)

WHITELIST = ["leopex1","Degenetive","SLCScannerBot","Steez431","cripplingdegen","ARC","MoneyMalicia"]
WL_SET = set(u.lower().lstrip("@") for u in WHITELIST)

def load_data():
    if os.path.exists(DATA_FILE):
        return json.load(open(DATA_FILE))
    return {"users": {}, "wallet_map": {}, "seen_tx": []}

def save_data(d):
    json.dump(d, open(DATA_FILE, "w"), indent=2)

def send_message(chat_id, text):
    try:
        requests.post(f"{TELE_BASE}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception:
        pass

def export_invite_link(chat_id):
    try:
        r = requests.post(f"{TELE_BASE}/exportChatInviteLink", json={"chat_id": chat_id}, timeout=10).json()
        return r.get("result")
    except Exception:
        return None

def kick_from_chat(chat_id, user_id):
    try:
        requests.post(f"{TELE_BASE}/kickChatMember", json={"chat_id": chat_id, "user_id": user_id}, timeout=10)
    except Exception:
        pass

def get_last_txs_for(address, limit=50):
    try:
        r = requests.get(f"{SOLSCAN_BASE}/account/transactions?account={address}&limit={limit}", timeout=10)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []

def get_tx_detail(signature):
    try:
        r = requests.get(f"{SOLSCAN_BASE}/transaction/{signature}", timeout=10)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}

data = load_data()

def norm_username(u):
    if not u: return ""
    return u.lower().lstrip("@")

def is_whitelisted(username):
    return norm_username(username) in WL_SET

def handle_new_payment(tx):
    sig = tx.get("txHash") or tx.get("signature")
    if not sig or sig in data.get("seen_tx", []):
        return
    detail = get_tx_detail(sig)
    data.setdefault("seen_tx", []).append(sig)
    try:
        lamports = 0
        for k in ("nativeTransfers","solTransfers","transfers","tokenTransfers","sol_transfer"):
            for item in (detail.get(k) or []):
                to = item.get("to") or item.get("destination") or item.get("tokenAddress")
                amt = item.get("amount") or item.get("lamports") or item.get("value")
                if to and to.lower() == WALLET.lower() and amt:
                    try:
                        lamports += int(amt)
                    except Exception:
                        try:
                            lamports += int(float(amt) * 1_000_000_000)
                        except Exception:
                            pass
        memo_found = "SLC30" in json.dumps(detail)
        if lamports >= MIN_LAMPORTS and memo_found:
            signer = (detail.get("feePayer") or detail.get("signer") or
                      (detail.get("transaction",{}) or {}).get("message",{}).get("accountKeys", [None])[0])
            if signer:
                username = data.get("wallet_map", {}).get(signer)
                if username:
                    grant_access_to(username, signer, sig)
    finally:
        save_data(data)

def grant_access_to(username, wallet_addr, signature):
    uname = username if username.startswith("@") else ("@" + username) if username.isalnum() else username
    u = data.setdefault("users", {}).setdefault(uname, {})
    now = datetime.utcnow().isoformat()
    if "join" not in u:
        u["join"] = now
    u["last_paid"] = now
    u["wallet"] = wallet_addr
    invite = export_invite_link(SLC_CHAT_ID)
    welcome = ("Welcome to the SLC Trench Scanner. This channel provides raw contract addresses scans from inside the SLC ecosystem in real-time. "
               "There is no delay, every CA inside the SLC will be immediately sent into here. You are only seeing the data, no theories, no due diligence, no inside info. "
               "There will be duplicates, there will be times of noise and times of silence. The alpha is there, the runner is there, it is up to you to find it. "
               "For full Discord access, inquire via DM at 431steez. Join the SLC, access the edge.")
    uid = u.get("user_id")
    target = uid if uid else uname
    send_message(target, welcome)
    if invite:
        send_message(target, f"Join the private SLC Trench Scanner here: {invite}")
    print(f"[{datetime.utcnow().isoformat()}] Granted access to {uname} for payment {signature}")
    save_data(data)

def poll_telegram_updates():
    offset = None
    while True:
        try:
            params = {"timeout":20, "limit":10}
            if offset: params["offset"] = offset
            r = requests.get(f"{TELE_BASE}/getUpdates", params=params, timeout=25).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message") or {}
                if not msg: continue
                text = msg.get("text","").strip()
                frm = msg.get("from", {})
                uname = ("@" + frm.get("username")) if frm.get("username") else str(frm.get("id"))
                uid = frm.get("id")
                if text.lower().startswith("/start"):
                    parts = text.split()
                    if len(parts) >= 2:
                        w = parts[1].strip()
                        data.setdefault("wallet_map", {})[w] = uname
                        u = data.setdefault("users", {}).setdefault(uname, {})
                        u.setdefault("join", datetime.utcnow().isoformat())
                        u["user_id"] = uid
                        u["wallet"] = w
                        save_data(data)
                        send_message(uid, f"Thanks {uname}. I mapped wallet `{w}` to your Telegram account. Now send payment of >=0.15 SOL with memo `SLC30` to {WALLET}.")
                    else:
                        u = data.setdefault("users", {}).setdefault(uname, {})
                        u.setdefault("join", datetime.utcnow().isoformat())
                        u["user_id"] = uid
                        save_data(data)
                        send_message(uid, "Send `/start <your_solana_wallet_address>` so I can match your payment. Example: `/start DqTx...`")
                elif text.lower().startswith("/myjoin"):
                    info = data.get("users", {}).get(uname)
                    if not info:
                        send_message(uid, "No join record found. Use `/start <wallet>` first before paying.")
                    else:
                        join = info.get("join"); last = info.get("last_paid")
                        expire = "N/A"
                        try:
                            if last:
                                expire = (datetime.fromisoformat(last) + timedelta(days=30)).isoformat()
                        except Exception:
                            pass
                        send_message(uid, f"Joined: {join}\nLast paid: {last}\nExpires: {expire}")
        except Exception as e:
            print("tg poll error", e)
            time.sleep(5)

def daily_expiry_check():
    while True:
        now = datetime.utcnow()
        next_mid = (now + timedelta(days=1)).replace(hour=0, minute=0, second=10, microsecond=0)
        time.sleep((next_mid - now).total_seconds())
        print(f"[{datetime.utcnow().isoformat()}] Running daily expiry sweep")
        changed = False
        for uname, info in list(data.get("users", {}).items()):
            if is_whitelisted(uname):
                continue
            last = info.get("last_paid") or info.get("join")
            if not last: continue
            try:
                last_dt = datetime.fromisoformat(last)
            except Exception:
                continue
            if datetime.utcnow() - last_dt > timedelta(days=30):
                user_id = info.get("user_id")
                if user_id:
                    kick_from_chat(SLC_CHAT_ID, user_id)
                    send_message(user_id, "Your 30-day access expired and you have been removed from the SLC Trench Scanner. Re-pay to regain access.")
                    print(f"Kicked {uname} ({user_id}) due to expiry")
                data["users"].pop(uname, None)
                for w,a in list(data.get("wallet_map", {}).items()):
                    if a == uname:
                        data["wallet_map"].pop(w, None)
                changed = True
        if changed:
            save_data(data)

def solscan_loop():
    while True:
        try:
            txs = get_last_txs_for(WALLET, limit=50)
            for tx in txs or []:
                handle_new_payment(tx)
        except Exception as e:
            print("solscan poll error", e)
        time.sleep(30)

if __name__ == "__main__":
    print("SLC Trench Scanner helper starting...")
    save_data(data)
    threading.Thread(target=poll_telegram_updates, daemon=True).start()
    threading.Thread(target=daily_expiry_check, daemon=True).start()
    try:
        solscan_loop()
    except KeyboardInterrupt:
        print("Stopping...")

