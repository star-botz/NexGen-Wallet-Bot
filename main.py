import os
import time
import random
import threading
import requests
import qrcode
import io
from pymongo import MongoClient
from datetime import datetime, timedelta
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from PIL import Image
import pytz

# ================== CONFIGURATION ==================
BOT_TOKEN = "7871383214:AAGvWN4WBT1PGMj2FixUlUdOwC7Aruclr-4"
BOT_USERNAME = "@NexGenXWalletbot"

OWNER_IDS = [7973809312]
ADMIN_IDS = [7973809312]

# Timezone
IST = pytz.timezone('Asia/Kolkata')

# MongoDB Configuration
MONGO_DB_URI = "mongodb+srv://starbotzofficial_db_user:ssHukAD790DMdDc5@star-deals.juuqpw5.mongodb.net/?retryWrites=true&w=majority&appName=Star-Deals"
client = MongoClient(MONGO_DB_URI)
db = client["N_wallet"]  # Wallet Database

# Collections
users_col = db["users"]        # User data (for broadcast/ban)
wallet_col = db["wallet"]       # Wallet balance data
payments_col = db["payments"]   # Payment history (deposits)
history_col = db["history"]     # Transaction history (credit/debit)
analysis_col = db["analysis"]   # Daily analysis

# Admin Logs Group
ADMIN_LOGS_GROUP = -1003323347497
ADMIN_LOGS_TOPIC = {
    'success_payment': 2,
    'failed_payment': 3,
    'admin_funds_add': 4,
    'daily_summary': 5,
    'analytics_bot': 5
}

# UPI Configuration
UPI_ID = 'paytm.s1wl90c@pty'
PAYTM_API_URL = 'https://paytm-api.litedns.xyz/?mid=YBXOxW63443729109038&oid={order_id}'

# QR Settings
QR_VALIDITY = 600  # 10 minutes
PAYMENT_VERIFICATION_INTERVAL = 3  # seconds

# ================== INITIALIZE BOT ==================
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# Active QR codes storage
active_qrs = {}

# Daily stats
daily_stats = {
    'total_amount': 0,
    'new_users': 0,
    'successful_payments': 0,
    'failed_payments': 0,
    'transactions': {}
}

# ================== DATABASE INDEXES ==================
users_col.create_index("user_id", unique=True)
wallet_col.create_index("user_id", unique=True)
payments_col.create_index([("user_id", 1), ("created_at", -1)])
history_col.create_index([("user_id", 1), ("created_at", -1)])
analysis_col.create_index("date", unique=True)

# ================== DATABASE FUNCTIONS ==================

def get_user(user_id):
    """Get user data from users_col"""
    return users_col.find_one({"user_id": user_id})

def update_user(user_id, update_data):
    """Update or create user in users_col"""
    users_col.update_one(
        {"user_id": user_id},
        {"$set": update_data},
        upsert=True
    )
    return get_user(user_id)

def get_wallet(user_id):
    """Get wallet data"""
    return wallet_col.find_one({"user_id": user_id})

def update_wallet(user_id, update_data):
    """Update wallet data"""
    wallet_col.update_one(
        {"user_id": user_id},
        {"$set": update_data},
        upsert=True
    )
    return get_wallet(user_id)

def add_to_wallet(user_id, amount, admin_id=None, order_id=None):
    """Add amount to wallet balance with order ID"""
    if not order_id:
        order_id = generate_admin_order_id(user_id, admin_id) if admin_id else generate_order_id(user_id)
    
    result = wallet_col.update_one(
        {"user_id": user_id},
        {
            "$inc": {
                "balance": amount,
                "total_deposit": amount
            },
            "$set": {
                "updated_at": datetime.now(IST).isoformat()
            }
        },
        upsert=True
    )
    
    # Add to history
    add_to_history(
        user_id=user_id,
        type="credit",
        amount=amount,
        service="Wallet Deposit" if not admin_id else f"Admin Added by {admin_id}",
        order_id=order_id
    )
    
    return result

def deduct_from_wallet(user_id, amount, service, order_id=None):
    """Deduct amount from wallet balance"""
    wallet = get_wallet(user_id)
    if not wallet or wallet.get('balance', 0) < amount:
        return False
    
    result = wallet_col.update_one(
        {"user_id": user_id, "balance": {"$gte": amount}},
        {
            "$inc": {
                "balance": -amount,
                "total_spent": amount
            },
            "$set": {
                "updated_at": datetime.now(IST).isoformat()
            }
        }
    )
    
    if result.modified_count > 0:
        # Add to history
        add_to_history(
            user_id=user_id,
            type="debit",
            amount=amount,
            service=service,
            order_id=order_id
        )
        return True
    
    return False

def add_to_history(user_id, type, amount, service, order_id=None):
    """Add transaction to history"""
    history_data = {
        "user_id": user_id,
        "type": type,  # 'credit' or 'debit'
        "amount": amount,
        "service": service,
        "order_id": order_id,
        "created_at": datetime.now(IST).isoformat()
    }
    return history_col.insert_one(history_data)

def get_user_history(user_id, limit=10):
    """Get user transaction history"""
    return list(history_col.find(
        {"user_id": user_id}
    ).sort("created_at", -1).limit(limit))

def save_payment(payment_data):
    """Save payment record"""
    return payments_col.insert_one(payment_data)

def update_analysis(amount, date_str):
    """Update daily analysis"""
    analysis_col.update_one(
        {"date": date_str},
        {
            "$inc": {
                "total_amount": amount,
                "total_transactions": 1
            }
        },
        upsert=True
    )

def get_all_users():
    """Get all user IDs for broadcast"""
    return list(users_col.find({}, {"user_id": 1}))

# ================== ORDER ID GENERATION ==================

def get_ist_time():
    """Get current IST time"""
    return datetime.now(IST)

def generate_order_id(user_id):
    """Generate order ID for wallet deposits"""
    timestamp = datetime.now(IST).strftime('%H%M%S')
    rand_num = random.randint(1000, 9999)
    return f"NEX-{user_id}-{timestamp}-{rand_num}"

def generate_admin_order_id(user_id, admin_id):
    """Generate order ID for admin added funds"""
    timestamp = datetime.now(IST).strftime('%H%M%S')
    rand_num = random.randint(1000, 9999)
    return f"ADM-{user_id}-{admin_id}-{timestamp}-{rand_num}"

# ================== QR FUNCTIONS ==================

def generate_blank_image():
    """Generate blank image for QR expiry"""
    img = Image.new('RGB', (250, 250), (255, 255, 255))
    byte_arr = io.BytesIO()
    img.save(byte_arr, format='PNG')
    byte_arr.seek(0)
    return byte_arr

BLANK_IMAGE = generate_blank_image()

def generate_upi_qr(order_id):
    """Generate QR code WITHOUT amount - user pays any amount"""
    txn_note = f"NexGen_{order_id}"
    payment_url = f"upi://pay?pa={UPI_ID}&pn=NexGenWallet&tr={order_id}&tn={txn_note}&cu=INR"
    
    qr = qrcode.QRCode(version=1, box_size=8, border=4)
    qr.add_data(payment_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return img_byte_arr

def verify_payment(order_id):
    """Verify payment and get ACTUAL amount paid by user"""
    try:
        response = requests.get(PAYTM_API_URL.format(order_id=order_id), timeout=10)
        data = response.json()
        
        if data.get('STATUS') == 'TXN_SUCCESS':
            amount = float(data.get('TXNAMOUNT', 0))
            return {
                'status': 'success',
                'amount': amount,
                'message': data.get('RESPMSG', 'Payment successful'),
                'txn_id': data.get('TXNID', ''),
                'txn_count': int(data.get('TXNCOUNT', 0))
            }
        elif data.get('STATUS') == 'PENDING':
            return {
                'status': 'pending',
                'message': 'Payment is pending',
                'txn_id': data.get('TXNID', '')
            }
        else:
            return {
                'status': 'failed',
                'message': data.get('RESPMSG', 'Payment not verified'),
                'txn_id': data.get('TXNID', '')
            }
    except Exception as e:
        print(f"Payment verification error: {e}")
        return {'status': 'error', 'message': str(e)}

# ================== QR EXPIRY HANDLER ==================

def qr_expiry_handler(user_id, qr_message_id, order_id):
    """Handle QR code expiry"""
    time.sleep(QR_VALIDITY)
    
    user_id_str = str(user_id)
    if user_id_str in active_qrs and active_qrs[user_id_str]['qr_message_id'] == qr_message_id:
        # Clear from active_qrs
        del active_qrs[user_id_str]
        
        try:
            # Delete QR message
            bot.delete_message(chat_id=user_id, message_id=qr_message_id)
            
            # Send expiry message
            expiry_text = (
                "⏰ <b>QR Code Expired</b>\n\n"
                "Your payment QR has expired because you didn't complete the payment within 10 minutes.\n\n"
                "You can generate a new QR code anytime."
            )
            
            bot.send_message(
                user_id,
                expiry_text,
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔄 Generate New QR", callback_data="generate_qr")
                )
            )
            
            # Log expired payment
            log_expired_payment(user_id, order_id)
        except Exception as e:
            print(f"Error in expiry handler: {e}")

# ================== PAYMENT VERIFICATION THREAD ==================

def payment_verification_thread(user_id, order_id, qr_message_id):
    """Thread to verify payment"""
    start_time = time.time()
    
    while time.time() - start_time < QR_VALIDITY:
        result = verify_payment(order_id)
        
        if result['status'] == 'success':
            handle_successful_payment(user_id, order_id, result, qr_message_id)
            return
        
        time.sleep(PAYMENT_VERIFICATION_INTERVAL)

# ================== HANDLE SUCCESSFUL PAYMENT ==================

def handle_successful_payment(user_id, order_id, payment_result, qr_message_id):
    """Handle successful payment"""
    user_id_str = str(user_id)
    amount = payment_result['amount']
    
    # Get or create user
    user = get_user(user_id)
    if not user:
        user = update_user(user_id, {
            "username": None,
            "first_name": None,
            "joined_at": datetime.now(IST).isoformat(),
            "is_banned": False
        })
        daily_stats['new_users'] += 1
    
    # Add amount to wallet with order ID
    add_to_wallet(user_id, amount, order_id=order_id)
    
    # Save payment record
    payment_data = {
        "payment_id": order_id,
        "user_id": user_id,
        "amount": amount,
        "status": "success",
        "method": "UPI",
        "txn_id": payment_result.get('txn_id', ''),
        "created_at": datetime.now(IST).isoformat()
    }
    save_payment(payment_data)
    
    # Update daily stats
    daily_stats['successful_payments'] += 1
    daily_stats['total_amount'] += amount
    if amount not in daily_stats['transactions']:
        daily_stats['transactions'][amount] = 0
    daily_stats['transactions'][amount] += 1
    
    # Update analysis
    today_str = datetime.now(IST).strftime('%Y-%m-%d')
    update_analysis(amount, today_str)
    
    # Delete QR message
    if qr_message_id:
        try:
            bot.delete_message(chat_id=user_id, message_id=qr_message_id)
        except:
            pass
        
    # Clear from active_qrs
    if user_id_str in active_qrs:
        del active_qrs[user_id_str]
    
    # Get user info for logging
    try:
        user_data = bot.get_chat(user_id)
        username = user_data.username if user_data.username else 'N/A'
        first_name = user_data.first_name
    except:
        username = 'N/A'
        first_name = 'Unknown'
    
    # Send success message to user
    success_msg = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>Payment Successful</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🧾 Order ID: <code>{order_id}</code>\n"
        f"💰 Amount: ₹{amount}\n"
        f"💳 Added to Wallet: ✅\n"
        f"📅 Date: {datetime.now(IST).strftime('%d-%m-%Y | %I:%M %p')}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Your wallet has been credited with ₹{amount}\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )
    
    bot.send_message(user_id, success_msg)
    
    # Log to admin group
    log_text = (
        f"✅ Payment Received!\n"
        f"👤 User: {first_name}\n"
        f"🔹 Username: @{username}\n"
        f"🆔 User ID: {user_id}\n"
        f"🛒 Order ID: <code>{order_id}</code>\n"
        f"💰 Amount: ₹{amount}\n"
        f"📌 Transaction ID: <code>{payment_result.get('txn_id', '')}</code>\n"
        f"📌 Status: ✅ Txn Success\n"
        f"📌 Txn Count: {payment_result.get('txn_count', 0)}"
    )
    
    try:
        bot.send_message(
            ADMIN_LOGS_GROUP,
            log_text,
            message_thread_id=ADMIN_LOGS_TOPIC['success_payment']
        )
    except:
        pass

def log_expired_payment(user_id, order_id):
    """Log expired payment"""
    try:
        user_data = bot.get_chat(user_id)
        username = user_data.username if user_data.username else 'N/A'
        first_name = user_data.first_name
    except:
        username = 'N/A'
        first_name = 'Unknown'
    
    expired_text = (
        f"⏰ Expired Transaction\n"
        f"👤 User: {first_name}\n"
        f"🔹 Username: @{username}\n"
        f"🆔 User ID: {user_id}\n"
        f"🛒 Order ID: <code>{order_id}</code>"
    )
    
    try:
        bot.send_message(
            ADMIN_LOGS_GROUP,
            expired_text,
            message_thread_id=ADMIN_LOGS_TOPIC['failed_payment']
        )
        daily_stats['failed_payments'] += 1
    except:
        pass

# ================== WALLET BALANCE FUNCTION ==================

def get_wallet_balance_text(user_id):
    """Get formatted wallet balance text"""
    wallet = get_wallet(user_id)
    
    if not wallet:
        balance = 0
        total_spent = 0
        total_deposit = 0
    else:
        balance = wallet.get('balance', 0)
        total_spent = wallet.get('total_spent', 0)
        total_deposit = wallet.get('total_deposit', 0)
    
    # Get user info
    try:
        user_data = bot.get_chat(user_id)
        first_name = user_data.first_name
    except:
        first_name = "User"
    
    balance_text = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <b>Your NexGen Wallet Balance</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 User: {first_name}\n"
        f"🆔 User ID: {user_id}\n\n"
        f"💰 <b>Available Balance : ₹{balance} INR</b>\n"
        f"🪙 NexGen Coin Value : {balance} NexGen Coins\n\n"
        f"💱 Conversion Rate:\n"
        f"1 NexGen Coin = ₹1 INR\n\n"
        f"📤 Total Spent: ₹{total_spent}\n"
        f"📥 Total Added: ₹{total_deposit}\n\n"
        f"🕒 Last Updated: {datetime.now(IST).strftime('%d-%m-%Y | %I:%M %p')}\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )
    
    return balance_text

# ================== DAILY SUMMARY ==================

def daily_summary_task():
    """Run daily summary at 23:59 IST"""
    while True:
        now = get_ist_time()
        if now.hour == 23 and now.minute == 59:
            send_daily_summary()
            reset_daily_stats()
        time.sleep(60)

def send_daily_summary():
    """Send daily summary to admin logs"""
    today_ist = get_ist_time()
    
    # Payment summary
    summary_text = "📊 Midnight Summary\n\n"
    total_transactions = 0
    
    for amount, count in daily_stats['transactions'].items():
        summary_text += f"{count} transactions × ₹{amount} = ₹{count * amount}\n"
        total_transactions += count
    
    summary_text += "---------------------------------\n"
    summary_text += f"🏦 Total Transactions: {total_transactions}\n"
    summary_text += f"💰 Total Amount: ₹{daily_stats['total_amount']}"
    
    try:
        bot.send_message(
            ADMIN_LOGS_GROUP,
            summary_text,
            message_thread_id=ADMIN_LOGS_TOPIC['daily_summary']
        )
    except:
        pass
    
    # Analytics summary
    analytics_text = (
        f"<b>Performance of bot at {today_ist.strftime('%d-%m-%Y')}</b>\n"
        f"<b>from 00:00 AM to 11:59 PM (IST)</b>\n\n"
        f"♎ Total payment: ₹{daily_stats['total_amount']}\n\n"
        f"👤 Total New users: {daily_stats['new_users']}\n\n"
        f"✅ Successful payments: {daily_stats['successful_payments']}\n\n"
        f"❌ Failed payments: {daily_stats['failed_payments']}"
    )
    
    try:
        bot.send_message(
            ADMIN_LOGS_GROUP,
            analytics_text,
            message_thread_id=ADMIN_LOGS_TOPIC['analytics_bot']
        )
    except:
        pass

def reset_daily_stats():
    """Reset daily stats"""
    global daily_stats
    daily_stats = {
        'total_amount': 0,
        'new_users': 0,
        'successful_payments': 0,
        'failed_payments': 0,
        'transactions': {}
    }

# ================== START COMMAND ==================

@bot.message_handler(commands=['start'])
def start_command(message):
    """Handle /start command"""
    user_id = message.from_user.id
    
    # Check if banned
    user = get_user(user_id)
    if user and user.get('is_banned'):
        bot.reply_to(message, "🚫 You are banned from using this bot.")
        return
    
    # Create or update user
    update_user(user_id, {
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_seen": datetime.now(IST).isoformat()
    })
    
    # Welcome message
    welcome_text = (
        f"👋 Welcome to NexGen Wallet Bot\n\n"
        f"💎 Official Wallet of NexGen Deals\n\n"
        f"🪙 Buy & Manage NexGen Coin\n"
        f"⚡ Instant Wallet System\n"
        f"🔒 100% Secure Payments\n\n"
        f"Tap below to continue 👇"
    )
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🛒 Start Payment", callback_data="start_payment"))
    
    bot.send_message(user_id, welcome_text, reply_markup=markup)
    
    # Send keyboard
    send_main_keyboard(user_id)

def send_main_keyboard(user_id):
    """Send main keyboard to user"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🛒 Start Payment")
    markup.add("💰 Wallet Balance", "📝 Transaction")
    markup.add("📃 Rules", "📞 Contact Team")
    
    bot.send_message(user_id, "Choose an option:", reply_markup=markup)

# ================== CALLBACK HANDLERS ==================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    """Handle all callback queries"""
    user_id = call.from_user.id
    user_id_str = str(user_id)
    data = call.data
    
    # Check if banned
    user = get_user(user_id)
    if user and user.get('is_banned'):
        bot.answer_callback_query(call.id, "🚫 You are banned")
        return
    
    if data == "start_payment":
        handle_start_payment(call)
    
    elif data == "generate_qr":
        handle_generate_qr(call)
    
    elif data == "refresh_wallet":
        handle_refresh_wallet(call)
    
    elif data.startswith("cancel_"):
        order_id = data.split("_", 1)[1]
        handle_cancel_payment(call, order_id)

def handle_start_payment(call):
    """Handle start payment button"""
    user_id = call.from_user.id
    
    # Check for existing pending payment
    user_id_str = str(user_id)
    if user_id_str in active_qrs:
        bot.answer_callback_query(
            call.id, 
            "⚠️ You already have a pending payment. Please complete or wait 10 minutes."
        )
        return
    
    payment_text = (
        f"🪙 NexGen Wallet Balance Added\n\n"
        f"💱 Conversion Rate:\n"
        f"1 NexGen Coin = ₹1 INR\n\n"
        f"Minimum Purchase: 1 Coins\n"
        f"Maximum Purchase: 1,00,000 Coins\n\n"
        f"Your coins will be credited instantly\n"
        f"after successful payment.\n\n"
        f"Click Generate QR and Pay Any Amount."
    )
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🛒 Generate QR", callback_data="generate_qr"))
    
    try:
        bot.edit_message_text(
            chat_id=user_id,
            message_id=call.message.message_id,
            text=payment_text,
            reply_markup=markup
        )
    except:
        bot.send_message(user_id, payment_text, reply_markup=markup)
    
    bot.answer_callback_query(call.id)

def handle_generate_qr(call):
    """Generate QR code for payment"""
    user_id = call.from_user.id
    user_id_str = str(user_id)
    
    # Check for existing pending payment
    if user_id_str in active_qrs:
        bot.answer_callback_query(
            call.id,
            "⚠️ You already have a pending payment. Please complete or wait 10 minutes."
        )
        return
    
    # Generate order ID
    order_id = generate_order_id(user_id)
    
    # Generate QR
    qr_img = generate_upi_qr(order_id)
    
    # Send QR message
    caption = (
        f"<b>Scan QR to Pay</b>\n\n"
        f"Order ID: <code>{order_id}</code>\n\n"
        f"💳 Pay any amount you want to add to your wallet.\n"
        f"⏱ QR expires in 10 minutes.\n\n"
        f"After payment, it will be automatically verified."
    )
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Cancel Payment", callback_data=f"cancel_{order_id}"))
    
    try:
        # Delete previous message if exists
        try:
            bot.delete_message(user_id, call.message.message_id)
        except:
            pass
        
        msg = bot.send_photo(
            user_id,
            qr_img,
            caption=caption,
            reply_markup=markup
        )
        
        # Store in active_qrs
        active_qrs[user_id_str] = {
            "qr_message_id": msg.message_id,
            "order_id": order_id,
            "created_at": time.time()
        }
        
        # Start verification thread
        threading.Thread(
            target=payment_verification_thread,
            args=(user_id, order_id, msg.message_id)
        ).start()
        
        # Start expiry thread
        threading.Thread(
            target=qr_expiry_handler,
            args=(user_id, msg.message_id, order_id)
        ).start()
        
        bot.answer_callback_query(call.id, "✅ QR Code Generated")
        
    except Exception as e:
        print(f"Error generating QR: {e}")
        bot.answer_callback_query(call.id, "❌ Error generating QR")

def handle_refresh_wallet(call):
    """Handle refresh wallet button"""
    user_id = call.from_user.id
    
    # Get fresh wallet balance text
    balance_text = get_wallet_balance_text(user_id)
    
    # Create refresh button markup
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔄 Refresh Wallet", callback_data="refresh_wallet"))
    
    try:
        bot.edit_message_text(
            chat_id=user_id,
            message_id=call.message.message_id,
            text=balance_text,
            reply_markup=markup
        )
        bot.answer_callback_query(call.id, "✅ Wallet Refreshed")
    except Exception as e:
        print(f"Error refreshing wallet: {e}")
        bot.answer_callback_query(call.id, "❌ Error refreshing")

def handle_cancel_payment(call, order_id):
    """Handle payment cancellation"""
    user_id = call.from_user.id
    user_id_str = str(user_id)
    
    # Check if this is the active payment
    if user_id_str in active_qrs and active_qrs[user_id_str]['order_id'] == order_id:
        # Delete QR message
        try:
            bot.delete_message(user_id, call.message.message_id)
        except:
            pass
        
        # Remove from active_qrs
        del active_qrs[user_id_str]
        
        # Send cancellation message
        cancel_text = (
            "❌ <b>Payment Cancelled</b>\n\n"
            "Your payment has been cancelled.\n"
            "You can generate a new QR code anytime."
        )
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🛒 Generate QR", callback_data="generate_qr"))
        
        bot.send_message(user_id, cancel_text, reply_markup=markup)
        bot.answer_callback_query(call.id, "✅ Payment cancelled")
    else:
        bot.answer_callback_query(call.id, "❌ No active payment found")

# ================== KEYBOARD HANDLERS ==================

@bot.message_handler(func=lambda message: message.text == "🛒 Start Payment")
def handle_start_payment_button(message):
    """Handle Start Payment button"""
    user_id = message.from_user.id
    
    # Check for existing pending payment
    user_id_str = str(user_id)
    if user_id_str in active_qrs:
        bot.reply_to(
            message,
            "⚠️ You already have a pending payment.\nPlease complete or wait 10 minutes."
        )
        return
    
    payment_text = (
        f"🪙 NexGen Wallet Balance Added\n\n"
        f"💱 Conversion Rate:\n"
        f"1 NexGen Coin = ₹1 INR\n\n"
        f"Minimum Purchase: 1 Coins\n"
        f"Maximum Purchase: 1,00,000 Coins\n\n"
        f"Your coins will be credited instantly\n"
        f"after successful payment.\n\n"
        f"Click Generate QR and Pay Any Amount."
    )
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🛒 Generate QR", callback_data="generate_qr"))
    
    bot.send_message(user_id, payment_text, reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "💰 Wallet Balance")
def handle_wallet_balance(message):
    """Handle Wallet Balance button with refresh option"""
    user_id = message.from_user.id
    
    # Get wallet balance text
    balance_text = get_wallet_balance_text(user_id)
    
    # Create refresh button markup
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔄 Refresh Wallet", callback_data="refresh_wallet"))
    
    bot.send_message(user_id, balance_text, reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "📝 Transaction")
def handle_transaction_history(message):
    """Handle Transaction History button"""
    user_id = message.from_user.id
    
    history = get_user_history(user_id, limit=10)
    
    if not history:
        bot.reply_to(message, "📭 No transaction history found.")
        return
    
    history_text = "━━━━━━━━━━━━━━━━━━━\n"
    history_text += "📝 <b>Your NexGen Transaction History</b>\n"
    history_text += "━━━━━━━━━━━━━━━━━━━\n\n"
    
    for tx in history:
        tx_type = tx.get('type', 'unknown')
        amount = tx.get('amount', 0)
        service = tx.get('service', 'Unknown')
        order_id = tx.get('order_id', 'N/A')
        
        try:
            created_at = datetime.fromisoformat(tx.get('created_at', ''))
            time_str = created_at.strftime('%d-%m-%Y | %I:%M %p')
        except:
            time_str = 'Unknown'
        
        if tx_type == 'credit':
            history_text += f"🟢 <b>Credit: ₹{amount}</b>\n"
        else:
            history_text += f"🔴 <b>Debit: ₹{amount}</b> ({service})\n"
        
        history_text += f"<code>NexPay Id: {order_id}</code>\n"
        history_text += f"🕒 {time_str}\n\n"
    
    bot.reply_to(message, history_text)

@bot.message_handler(func=lambda message: message.text == "📃 Rules")
def handle_rules(message):
    """Handle Rules button"""
    rules_text = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📃 <b>NexGen Wallet Rules</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"1️⃣ All payments are final. No refund policy.\n\n"
        f"2️⃣ Fake payment / Fake screenshot = Permanent Ban 🚫\n\n"
        f"3️⃣ Aap jo payment karoge vo refund nahi hoga.\n"
        f"   Uska use aapko hamare bots par hi karna hoga.\n\n"
        f"4️⃣ Wallet balance ko sirf NexGen official bots par use kiya ja sakta hai.\n\n"
        f"5️⃣ Wallet balance refundable nahi hai.\n\n"
        f"6️⃣ Rules admin policy ke hisab se honge.\n\n"
        f"7️⃣ Any misuse / fraud activity = Account suspension.\n\n"
        f"8️⃣ Respect Support Team & Follow instructions.\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Using this wallet means you agree to all above rules.\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )
    
    bot.reply_to(message, rules_text)

@bot.message_handler(func=lambda message: message.text == "📞 Contact Team")
def handle_contact(message):
    """Handle Contact Team button"""
    contact_text = (
        f"📞 <b>Need Help?</b>\n\n"
        f"Contact Our Support Team:\n"
        f"@NexGenSupport"
    )
    
    bot.reply_to(message, contact_text)

# ================== ADMIN COMMANDS ==================

@bot.message_handler(commands=['admin'])
def admin_command(message):
    """Handle admin command"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Admin access required")
        return
    
    admin_text = (
        f"<b>Admin Panel</b>\n\n"
        f"Commands:\n\n"
        f"<code>/Add_Fund user_id amount</code> - Add funds to user wallet (Owner only)\n"
        f"<code>/Chk_order order_id</code> - Check order details\n"
        f"<code>/chk_user user_id</code> - Check user details\n"
        f"<code>/broadcast</code> - Broadcast message to all users\n"
        f"<code>/stats</code> - View bot statistics"
    )
    
    bot.reply_to(message, admin_text)

@bot.message_handler(commands=['Add_Fund'])
def add_fund_command(message):
    """Add funds to user wallet with order ID generation (Owner only)"""
    user_id = message.from_user.id
    
    # Check if in admin topic or owner only
    if message.chat.id != ADMIN_LOGS_GROUP or message.message_thread_id != ADMIN_LOGS_TOPIC['admin_funds_add']:
        if user_id not in OWNER_IDS:
            bot.reply_to(message, "❌ This command can only be used in admin funds topic")
            return
    
    if user_id not in OWNER_IDS:
        bot.reply_to(message, "❌ Owner access required")
        return
    
    try:
        args = message.text.split()
        if len(args) != 3:
            bot.reply_to(message, "❌ Format: /Add_Fund user_id amount")
            return
        
        target_user_id = int(args[1])
        amount = int(args[2])
        
        if amount <= 0:
            bot.reply_to(message, "❌ Amount must be positive")
            return
        
        # Generate order ID for admin addition
        order_id = generate_admin_order_id(target_user_id, user_id)
        
        # Add to wallet with order ID
        add_to_wallet(target_user_id, amount, admin_id=user_id, order_id=order_id)
        
        # Get admin info
        try:
            admin_data = bot.get_chat(user_id)
            admin_name = admin_data.first_name
        except:
            admin_name = f"Admin {user_id}"
        
        # Log to admin topic
        log_text = (
            f"💰 Funds Added by Admin\n\n"
            f"📦 Order ID: <code>{order_id}</code>\n"
            f"👤 Target User: {target_user_id}\n"
            f"💰 Amount: ₹{amount}\n"
            f"👨‍💼 Added By: {admin_name} [{user_id}]\n"
            f"🕒 Time: {datetime.now(IST).strftime('%d-%m-%Y %I:%M %p')}"
        )
        
        bot.send_message(
            ADMIN_LOGS_GROUP,
            log_text,
            message_thread_id=ADMIN_LOGS_TOPIC['admin_funds_add']
        )
        
        # Notify user
        try:
            # Get user info for personalized message
            user_data = bot.get_chat(target_user_id)
            first_name = user_data.first_name
        except:
            first_name = "User"
        
        user_notify = (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Wallet Updated by Admin</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Hello {first_name}!\n\n"
            f"📦 Order ID: <code>{order_id}</code>\n"
            f"💰 Amount Added: ₹{amount}\n"
            f"👨‍💼 Added By: Admin\n\n"
            f"💳 New Balance: Check /start\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        bot.send_message(target_user_id, user_notify)
        
        bot.reply_to(message, f"✅ Added ₹{amount} to user {target_user_id}\n📦 Order ID: {order_id}")
        
    except ValueError:
        bot.reply_to(message, "❌ Invalid amount or user ID")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['Chk_order'])
def check_order_command(message):
    """Check order details"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Admin access required")
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            bot.reply_to(message, "❌ Format: /Chk_order order_id")
            return
        
        order_id = args[1]
        
        # Check in payments (deposits)
        payment = payments_col.find_one({"payment_id": order_id})
        if payment:
            order_text = (
                f"📦 <b>Payment Details</b>\n\n"
                f"Order ID: <code>{payment['payment_id']}</code>\n"
                f"User ID: {payment['user_id']}\n"
                f"Amount: ₹{payment['amount']}\n"
                f"Status: {payment['status']}\n"
                f"Method: {payment['method']}\n"
                f"Txn ID: <code>{payment.get('txn_id', 'N/A')}</code>\n"
                f"Created: {payment['created_at']}"
            )
            bot.reply_to(message, order_text)
            return
        
        # Check in history (debit/credit transactions)
        history = history_col.find_one({"order_id": order_id})
        if history:
            order_text = (
                f"📦 <b>Transaction Details</b>\n\n"
                f"Order ID: <code>{order_id}</code>\n"
                f"User ID: {history['user_id']}\n"
                f"Type: {history['type']}\n"
                f"Amount: ₹{history['amount']}\n"
                f"Service: {history['service']}\n"
                f"Created: {history['created_at']}"
            )
            bot.reply_to(message, order_text)
            return
        
        bot.reply_to(message, f"❌ Order not found: {order_id}")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['chk_user'])
def check_user_command(message):
    """Check user details"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Admin access required")
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            bot.reply_to(message, "❌ Format: /chk_user user_id")
            return
        
        target_user_id = int(args[1])
        
        user = get_user(target_user_id)
        wallet = get_wallet(target_user_id)
        
        if not user:
            bot.reply_to(message, f"❌ User not found: {target_user_id}")
            return
        
        # Get user info
        try:
            user_data = bot.get_chat(target_user_id)
            username = user_data.username if user_data.username else 'N/A'
            first_name = user_data.first_name
        except:
            username = 'N/A'
            first_name = 'Unknown'
        
        # Get stats
        balance = wallet.get('balance', 0) if wallet else 0
        total_deposit = wallet.get('total_deposit', 0) if wallet else 0
        total_spent = wallet.get('total_spent', 0) if wallet else 0
        
        # Get payment count
        payment_count = payments_col.count_documents({"user_id": target_user_id})
        
        user_text = (
            f"👤 <b>User Information</b>\n\n"
            f"User ID: {target_user_id}\n"
            f"Name: {first_name}\n"
            f"Username: @{username}\n"
            f"Banned: {'Yes' if user.get('is_banned') else 'No'}\n"
            f"Joined: {user.get('joined_at', 'N/A')}\n\n"
            f"💰 <b>Wallet</b>\n"
            f"Balance: ₹{balance}\n"
            f"Total Deposit: ₹{total_deposit}\n"
            f"Total Spent: ₹{total_spent}\n"
            f"Payments Made: {payment_count}"
        )
        
        bot.reply_to(message, user_text)
        
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    """Broadcast message to all users"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Admin access required")
        return
    
    msg = bot.reply_to(
        message,
        "📢 Send the message you want to broadcast to all users.\n"
        "You can send text, photo, video, or any media."
    )
    bot.register_next_step_handler(msg, process_broadcast)

def process_broadcast(message):
    """Process broadcast message"""
    admin_id = message.from_user.id
    
    # Get all users
    users = get_all_users()
    
    if not users:
        bot.reply_to(message, "❌ No users found")
        return
    
    sent = 0
    failed = 0
    
    status_msg = bot.reply_to(message, f"📢 Broadcasting to {len(users)} users...")
    
    for user in users:
        try:
            if message.content_type == 'text':
                bot.send_message(user['user_id'], message.text)
            elif message.content_type == 'photo':
                bot.send_photo(
                    user['user_id'],
                    message.photo[-1].file_id,
                    caption=message.caption
                )
            elif message.content_type == 'video':
                bot.send_video(
                    user['user_id'],
                    message.video.file_id,
                    caption=message.caption
                )
            elif message.content_type == 'document':
                bot.send_document(
                    user['user_id'],
                    message.document.file_id,
                    caption=message.caption
                )
            sent += 1
        except:
            failed += 1
        
        time.sleep(0.05)  # Rate limit
    
    bot.edit_message_text(
        f"✅ Broadcast completed!\n\n"
        f"Sent: {sent}\n"
        f"Failed: {failed}",
        chat_id=status_msg.chat.id,
        message_id=status_msg.message_id
    )

@bot.message_handler(commands=['stats'])
def stats_command(message):
    """Show bot statistics"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Admin access required")
        return
    
    total_users = users_col.count_documents({})
    total_wallets = wallet_col.count_documents({})
    
    # Total balance across all wallets
    pipeline = [{"$group": {"_id": None, "total": {"$sum": "$balance"}}}]
    result = list(wallet_col.aggregate(pipeline))
    total_balance = result[0]['total'] if result else 0
    
    # Today's stats
    today_str = datetime.now(IST).strftime('%Y-%m-%d')
    today_analysis = analysis_col.find_one({"date": today_str})
    
    today_amount = today_analysis['total_amount'] if today_analysis else 0
    today_txns = today_analysis['total_transactions'] if today_analysis else 0
    
    stats_text = (
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total Users: {total_users}\n"
        f"💰 Active Wallets: {total_wallets}\n"
        f"💵 Total Balance: ₹{total_balance}\n\n"
        f"📅 <b>Today ({today_str})</b>\n"
        f"Amount: ₹{today_amount}\n"
        f"Transactions: {today_txns}\n\n"
        f"🔄 <b>Daily Stats (Current)</b>\n"
        f"Amount: ₹{daily_stats['total_amount']}\n"
        f"Success: {daily_stats['successful_payments']}\n"
        f"Failed: {daily_stats['failed_payments']}\n"
        f"New Users: {daily_stats['new_users']}"
    )
    
    bot.reply_to(message, stats_text)

# ================== BAN/UNBAN FUNCTIONS ==================

@bot.message_handler(commands=['ban'])
def ban_user(message):
    """Ban a user (Admin only)"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Admin access required")
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            bot.reply_to(message, "❌ Format: /ban user_id")
            return
        
        target_user_id = int(args[1])
        
        update_user(target_user_id, {"is_banned": True})
        
        bot.reply_to(message, f"✅ User {target_user_id} has been banned")
        
        # Notify user
        try:
            bot.send_message(target_user_id, "🚫 You have been banned from using this bot.")
        except:
            pass
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['unban'])
def unban_user(message):
    """Unban a user (Admin only)"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Admin access required")
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            bot.reply_to(message, "❌ Format: /unban user_id")
            return
        
        target_user_id = int(args[1])
        
        update_user(target_user_id, {"is_banned": False})
        
        bot.reply_to(message, f"✅ User {target_user_id} has been unbanned")
        
        # Notify user
        try:
            bot.send_message(target_user_id, "🟢 You have been unbanned.")
        except:
            pass
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# ================== START BACKGROUND TASKS ==================

threading.Thread(target=daily_summary_task, daemon=True).start()

# ================== START BOT ==================

if __name__ == "__main__":
    print("NexGen Wallet Bot started...")
    print(f"Bot Username: {BOT_USERNAME}")
    print(f"Owner IDs: {OWNER_IDS}")
    print(f"Admin IDs: {ADMIN_IDS}")
    
    while True:
        try:
            bot.infinity_polling()
        except Exception as e:
            print(f"Critical error: {e}. Restarting in 10 seconds...")
            time.sleep(10)