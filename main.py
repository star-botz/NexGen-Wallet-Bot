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
from telebot.types import (InlineKeyboardMarkup, InlineKeyboardButton, 
                          ReplyKeyboardMarkup)
from PIL import Image
import pytz

# ================== CONFIGURATION ==================
TOKEN = "8136602152:AAE3OSp90istOdoESCiIRd_RosDX_V3ws4I"  # Wallet Bot ka alag token
OWNER_ID = [7937954612]
ADMIN_ID = [7937954612]
ADMINS = OWNER_ID + ADMIN_ID

# UPI Configuration
UPI_ID = 'paytm.s1wl90c@pty'
PAYTM_API_URL = 'https://paytm-api.litedns.xyz/?mid=YBXOxW63443729109038&oid={order_id}'

# Group Configuration
ADMIN_LOGS_GROUP = -1002882027888
ADMIN_LOGS_TOPIC = {
    'success_payment': 3,
    'failed_payment': 4,
    'admin_funds_add': 5,
    'daily_summary': 6,
    'analytics_bot': 6
}

# MongoDB Configuration
MONGO_DB_URI = "mongodb+srv://starbotzofficial_db_user:ssHukAD790DMdDc5@star-deals.juuqpw5.mongodb.net/?retryWrites=true&w=majority&appName=Star-Deals"
client = MongoClient(MONGO_DB_URI)
db = client["N_wallet"]

# Collections
walletusers_col = db["walletusers"]  # Users info & ban data
wallet_col = db["wallet"]            # Wallet balances
payments_col = db["payments"]        # Payment history
history_col = db["history"]           # Transaction history
analysis_col = db["analysis"]         # Daily analysis

# IST Timezone
IST = pytz.timezone('Asia/Kolkata')

# Active QR tracking
active_qrs = {}
QR_VALIDITY = 600  # 10 minutes
PAYMENT_VERIFICATION_INTERVAL = 3

# Daily stats
daily_stats = {
    'total_amount': 0,
    'new_users': 0,
    'successful_payments': 0,
    'failed_payments': 0,
    'transactions': {}
}

# ================== BOT INITIALIZATION ==================
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# ================== HELPER FUNCTIONS ==================
def get_ist_time():
    return datetime.now(IST)

def generate_blank_image():
    img = Image.new('RGB', (250, 250), (255, 255, 255))
    byte_arr = io.BytesIO()
    img.save(byte_arr, format='PNG')
    byte_arr.seek(0)
    return byte_arr

BLANK_IMAGE = generate_blank_image()

def generate_order_id(user_id):
    timestamp = get_ist_time().strftime('%H%M%S')
    rand_num = random.randint(1000, 9999)
    return f"NEX-{user_id}-{timestamp}-{rand_num}"

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

# ================== DATABASE FUNCTIONS ==================
def get_wallet_user(user_id):
    return walletusers_col.find_one({"user_id": user_id})

def update_wallet_user(user_id, update_data):
    walletusers_col.update_one(
        {"user_id": user_id},
        {"$set": update_data},
        upsert=True
    )
    return get_wallet_user(user_id)

def get_wallet(user_id):
    return wallet_col.find_one({"user_id": user_id})

def add_to_wallet(user_id, amount, payment_id):
    """Add funds to wallet"""
    result = wallet_col.update_one(
        {"user_id": user_id},
        {
            "$inc": {
                "balance": amount,
                "total_deposit": amount
            },
            "$set": {"updated_at": get_ist_time().isoformat()}
        },
        upsert=True
    )
    
    # Add to history
    history_col.insert_one({
        "user_id": user_id,
        "type": "credit",
        "amount": amount,
        "payment_id": payment_id,
        "created_at": get_ist_time().isoformat()
    })
    
    return get_wallet(user_id)

def deduct_from_wallet(user_id, amount, order_id, service=""):
    """Deduct funds from wallet"""
    wallet = get_wallet(user_id)
    if not wallet or wallet.get("balance", 0) < amount:
        return False
    
    result = wallet_col.update_one(
        {"user_id": user_id},
        {
            "$inc": {
                "balance": -amount,
                "total_spent": amount
            },
            "$set": {"updated_at": get_ist_time().isoformat()}
        }
    )
    
    # Add to history
    history_col.insert_one({
        "user_id": user_id,
        "type": "debit",
        "amount": amount,
        "order_id": order_id,
        "service": service,
        "created_at": get_ist_time().isoformat()
    })
    
    return True

def save_payment(user_id, order_id, amount, status, txn_id=""):
    """Save payment record"""
    payment_data = {
        "payment_id": order_id,
        "user_id": user_id,
        "amount": amount,
        "status": status,
        "method": "UPI",
        "txn_id": txn_id,
        "created_at": get_ist_time().isoformat()
    }
    payments_col.insert_one(payment_data)
    return payment_data

def get_all_wallet_users():
    return list(walletusers_col.find({}, {"user_id": 1}))

# ================== QR EXPIRY HANDLER ==================
def qr_expiry_handler(user_id, qr_message_id, order_id):
    time.sleep(QR_VALIDITY)
    
    user_id_str = str(user_id)
    if user_id_str in active_qrs and active_qrs[user_id_str]['qr_message_id'] == qr_message_id:
        try:
            bot.delete_message(chat_id=user_id, message_id=qr_message_id)
            
            expiry_text = (
                "⏰ <b>QR Code Expired</b>\n\n"
                "Your payment QR has expired because you didn't complete the payment within 10 minutes.\n\n"
                "Click below to generate a new QR."
            )
            
            bot.send_message(
                user_id,
                expiry_text,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔄 Generate New QR", callback_data="start_payment")
                )
            )
            
            # Log expired payment
            log_expired_payment(user_id, order_id)
            
            # Clean up
            del active_qrs[user_id_str]
            
        except Exception as e:
            print(f"Error in expiry handler: {e}")

# ================== PAYMENT VERIFICATION THREAD ==================
def payment_verification_thread(user_id, order_id, qr_message_id):
    start_time = time.time()
    
    while time.time() - start_time < QR_VALIDITY:
        result = verify_payment(order_id)
        
        if result['status'] == 'success':
            handle_successful_payment(user_id, order_id, result, qr_message_id)
            return
        elif result['status'] == 'failed':
            time.sleep(PAYMENT_VERIFICATION_INTERVAL)
        else:
            time.sleep(PAYMENT_VERIFICATION_INTERVAL)
    
    # If we reach here, payment timed out
    user_id_str = str(user_id)
    if user_id_str in active_qrs and active_qrs[user_id_str]['order_id'] == order_id:
        handle_failed_payment(user_id, order_id, qr_message_id)

# ================== PAYMENT HANDLERS ==================
def handle_successful_payment(user_id, order_id, payment_result, qr_message_id):
    user_id_str = str(user_id)
    amount = payment_result['amount']
    txn_id = payment_result.get('txn_id', '')
    txn_count = payment_result.get('txn_count', 0)
    
    # Save payment record
    save_payment(user_id, order_id, amount, 'success', txn_id)
    
    # Add to wallet
    wallet = add_to_wallet(user_id, amount, order_id)
    
    # Update daily stats
    daily_stats['successful_payments'] += 1
    daily_stats['total_amount'] += amount
    if amount not in daily_stats['transactions']:
        daily_stats['transactions'][amount] = 0
    daily_stats['transactions'][amount] += 1
    
    # Delete QR message
    try:
        bot.delete_message(chat_id=user_id, message_id=qr_message_id)
    except:
        pass
    
    # Clean up active QR
    if user_id_str in active_qrs:
        del active_qrs[user_id_str]
    
    # Send success message to user
    success_text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "✅ Payment Successful\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"🧾 Order ID: {order_id}\n"
        f"💰 Amount: ₹{amount}\n"
        f"📅 Date: {get_ist_time().strftime('%d-%m-%Y')}\n\n"
        f"💼 Updated Balance: ₹{wallet.get('balance', 0)}\n"
        "━━━━━━━━━━━━━━━━━━━"
    )
    
    bot.send_message(user_id, success_text)
    
    # Log to admin group
    log_successful_payment(user_id, order_id, amount, txn_id, txn_count)

def handle_failed_payment(user_id, order_id, qr_message_id):
    user_id_str = str(user_id)
    
    # Save failed payment
    save_payment(user_id, order_id, 0, 'failed')
    
    # Update daily stats
    daily_stats['failed_payments'] += 1
    
    # Delete QR message
    try:
        bot.delete_message(chat_id=user_id, message_id=qr_message_id)
    except:
        pass
    
    # Clean up active QR
    if user_id_str in active_qrs:
        del active_qrs[user_id_str]
    
    # Send failure message
    failure_text = (
        "❌ <b>Payment Failed</b>\n\n"
        "Your payment could not be verified.\n"
        "Please try again or contact support."
    )
    
    bot.send_message(
        user_id,
        failure_text,
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔄 Try Again", callback_data="start_payment")
        )
    )
    
    # Log to admin
    log_failed_payment(user_id, order_id)

def handle_cancelled_payment(user_id, order_id, qr_message_id):
    user_id_str = str(user_id)
    
    # Delete QR message
    try:
        bot.delete_message(chat_id=user_id, message_id=qr_message_id)
    except:
        pass
    
    # Clean up active QR
    if user_id_str in active_qrs:
        del active_qrs[user_id_str]
    
    # Send cancellation message
    cancel_text = (
        "❌ <b>Payment Cancelled</b>\n\n"
        "Your payment has been cancelled.\n"
        "You can generate a new QR anytime."
    )
    
    bot.send_message(
        user_id,
        cancel_text,
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔄 Generate New QR", callback_data="start_payment")
        )
    )

# ================== LOGGING FUNCTIONS ==================
def log_successful_payment(user_id, order_id, amount, txn_id, txn_count):
    try:
        user_data = bot.get_chat(user_id)
        username = user_data.username if user_data.username else 'N/A'
        full_name = f"{user_data.first_name} {user_data.last_name or ''}"
    except:
        username = 'N/A'
        full_name = 'Unknown'
    
    success_text = (
        "✅ Payment Received!\n"
        f"👤 User: {full_name}\n"
        f"🔹 Username: @{username}\n"
        f"🆔 User ID: {user_id}\n"
        f"🛒 Order ID: {order_id}\n"
        f"💰 Amount: ₹{amount}\n"
        f"📌 Transaction ID: {txn_id}\n"
        f"📌 Status: ✅ Txn Success\n"
        f"📌 Txn Count: {txn_count}"
    )
    
    try:
        bot.send_message(
            ADMIN_LOGS_GROUP,
            success_text,
            message_thread_id=ADMIN_LOGS_TOPIC['success_payment']
        )
    except:
        pass

def log_failed_payment(user_id, order_id):
    try:
        user_data = bot.get_chat(user_id)
        username = user_data.username if user_data.username else 'N/A'
        full_name = f"{user_data.first_name} {user_data.last_name or ''}"
    except:
        username = 'N/A'
        full_name = 'Unknown'
    
    failed_text = (
        "❌ Failed Transaction\n"
        f"👤 User: {full_name}\n"
        f"🔹 Username: @{username}\n"
        f"🆔 User ID: {user_id}\n"
        f"🛒 Order ID: {order_id}"
    )
    
    try:
        bot.send_message(
            ADMIN_LOGS_GROUP,
            failed_text,
            message_thread_id=ADMIN_LOGS_TOPIC['failed_payment']
        )
    except:
        pass

def log_expired_payment(user_id, order_id):
    try:
        user_data = bot.get_chat(user_id)
        username = user_data.username if user_data.username else 'N/A'
        full_name = f"{user_data.first_name} {user_data.last_name or ''}"
    except:
        username = 'N/A'
        full_name = 'Unknown'
    
    expired_text = (
        "⏰ Expired Transaction\n"
        f"👤 User: {full_name}\n"
        f"🔹 Username: @{username}\n"
        f"🆔 User ID: {user_id}\n"
        f"🛒 Order ID: {order_id}"
    )
    
    try:
        bot.send_message(
            ADMIN_LOGS_GROUP,
            expired_text,
            message_thread_id=ADMIN_LOGS_TOPIC['failed_payment']
        )
    except:
        pass

def log_fund_add(user_id, amount, added_by, order_id):
    """Log when admin adds funds manually"""
    try:
        admin_data = bot.get_chat(added_by)
        admin_name = admin_data.first_name
        if admin_data.last_name:
            admin_name += f" {admin_data.last_name}"
    except:
        admin_name = f"Admin {added_by}"
    
    try:
        user_data = bot.get_chat(user_id)
        username = user_data.username if user_data.username else 'N/A'
        full_name = f"{user_data.first_name} {user_data.last_name or ''}"
    except:
        username = 'N/A'
        full_name = 'Unknown'
    
    fund_text = (
        "💰 Funds Added by Admin\n\n"
        f"👤 User: {full_name}\n"
        f"🔹 Username: @{username}\n"
        f"🆔 User ID: {user_id}\n"
        f"💰 Amount: ₹{amount}\n"
        f"🛒 Order ID: {order_id}\n"
        f"👨‍💼 Added By: {admin_name} [{added_by}]\n"
        f"📅 Date: {get_ist_time().strftime('%d-%m-%Y %H:%M:%S')}"
    )
    
    try:
        bot.send_message(
            ADMIN_LOGS_GROUP,
            fund_text,
            message_thread_id=ADMIN_LOGS_TOPIC['admin_funds_add']
        )
    except:
        pass

# ================== DAILY SUMMARY TASKS ==================
def daily_summary_task():
    while True:
        now = get_ist_time()
        if now.hour == 23 and now.minute == 59:
            send_daily_summary()
            reset_daily_stats()
        time.sleep(60)

def send_daily_summary():
    today_ist = get_ist_time()
    
    # Transaction summary
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
    
    # Analytics
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
    
    # Save to analysis collection
    analysis_col.insert_one({
        "date": today_ist.strftime('%Y-%m-%d'),
        "total_amount": daily_stats['total_amount'],
        "new_users": daily_stats['new_users'],
        "successful_payments": daily_stats['successful_payments'],
        "failed_payments": daily_stats['failed_payments'],
        "transactions": daily_stats['transactions'],
        "created_at": today_ist.isoformat()
    })

def reset_daily_stats():
    global daily_stats
    daily_stats = {
        'total_amount': 0,
        'new_users': 0,
        'successful_payments': 0,
        'failed_payments': 0,
        'transactions': {}
    }

# ================== ADMIN COMMANDS ==================
@bot.message_handler(commands=['admin'])
def admin_command(message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Admin access required")
        return
    
    # Check if in correct topic
    if message.chat.id != ADMIN_LOGS_GROUP or message.message_thread_id != ADMIN_LOGS_TOPIC['admin_funds_add']:
        bot.reply_to(message, "❌ This command only works in admin_funds_add topic")
        return
    
    admin_text = (
        "👋 <b>Admin Commands</b>\n\n"
        "<b>OWNER ONLY:</b>\n"
        "/Add_Fund <user_id> <amount> - Add funds to user wallet\n\n"
        "<b>OWNER & ADMIN:</b>\n"
        "/chk_order <order_id> - Check payment/transaction\n"
        "/chk_user <user_id> - Check user details\n"
        "/broadcast - Send message to all users"
    )
    
    bot.reply_to(message, admin_text, parse_mode='HTML')

@bot.message_handler(commands=['Add_Fund'])
def add_fund_command(message):
    user_id = message.from_user.id
    
    # Owner only
    if user_id not in OWNER_ID:
        bot.reply_to(message, "❌ Owner access required")
        return
    
    # Check if in correct topic
    if message.chat.id != ADMIN_LOGS_GROUP or message.message_thread_id != ADMIN_LOGS_TOPIC['admin_funds_add']:
        bot.reply_to(message, "❌ This command only works in admin_funds_add topic")
        return
    
    try:
        args = message.text.split()
        if len(args) < 3:
            bot.reply_to(message, "❌ Format: /Add_Fund <user_id> <amount>")
            return
        
        target_user_id = int(args[1])
        amount = float(args[2])
        
        if amount <= 0:
            bot.reply_to(message, "❌ Amount must be positive")
            return
        
        # Generate order ID for this addition
        order_id = generate_order_id(target_user_id).replace("NEX", "ADM")
        
        # Add to wallet
        wallet = add_to_wallet(target_user_id, amount, order_id)
        
        # Update user in walletusers if not exists
        try:
            user_data = bot.get_chat(target_user_id)
            username = user_data.username if user_data.username else ''
            first_name = user_data.first_name
        except:
            username = ''
            first_name = 'Unknown'
        
        update_wallet_user(target_user_id, {
            "username": username,
            "first_name": first_name,
            "updated_at": get_ist_time().isoformat()
        })
        
        # Log the addition
        log_fund_add(target_user_id, amount, user_id, order_id)
        
        # Notify user
        try:
            notify_text = (
                "━━━━━━━━━━━━━━━━━━━\n"
                "💰 Funds Added to Your Wallet\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"➕ Amount Added: ₹{amount}\n"
                f"💼 New Balance: ₹{wallet.get('balance', 0)}\n"
                f"🆔 Order ID: {order_id}\n\n"
                "━━━━━━━━━━━━━━━━━━━"
            )
            bot.send_message(target_user_id, notify_text)
        except:
            pass
        
        bot.reply_to(message, f"✅ Added ₹{amount} to user {target_user_id}\nNew Balance: ₹{wallet.get('balance', 0)}")
        
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID or amount")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['chk_user'])
def chk_user_command(message):
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Admin access required")
        return
    
    # Check if in correct topic
    if message.chat.id != ADMIN_LOGS_GROUP or message.message_thread_id != ADMIN_LOGS_TOPIC['admin_funds_add']:
        bot.reply_to(message, "❌ This command only works in admin_funds_add topic")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "❌ Format: /chk_user <user_id>")
            return
        
        target_user_id = int(args[1])
        
        # Get user info
        wallet_user = get_wallet_user(target_user_id)
        wallet = get_wallet(target_user_id)
        
        if not wallet_user:
            bot.reply_to(message, f"❌ User not found: {target_user_id}")
            return
        
        # Get user's transactions
        credits = list(history_col.find({"user_id": target_user_id, "type": "credit"}).sort("created_at", -1).limit(5))
        debits = list(history_col.find({"user_id": target_user_id, "type": "debit"}).sort("created_at", -1).limit(5))
        
        response = (
            f"👤 <b>User Details</b>\n\n"
            f"🆔 User ID: {target_user_id}\n"
            f"👤 Name: {wallet_user.get('first_name', 'Unknown')}\n"
            f"🔹 Username: @{wallet_user.get('username', 'N/A')}\n"
            f"🚫 Banned: {'Yes' if wallet_user.get('is_banned') else 'No'}\n"
            f"📅 Joined: {wallet_user.get('joined_at', 'N/A')}\n\n"
            f"💼 <b>Wallet</b>\n"
            f"💰 Balance: ₹{wallet.get('balance', 0) if wallet else 0}\n"
            f"📥 Total Deposits: ₹{wallet.get('total_deposit', 0) if wallet else 0}\n"
            f"📤 Total Spent: ₹{wallet.get('total_spent', 0) if wallet else 0}\n"
            f"🕒 Last Updated: {wallet.get('updated_at', 'N/A') if wallet else 'N/A'}\n\n"
        )
        
        if credits:
            response += "📥 <b>Recent Credits:</b>\n"
            for c in credits[:3]:
                response += f"• +₹{c['amount']} ({c['created_at'][:10]})\n"
        
        if debits:
            response += "\n📤 <b>Recent Debits:</b>\n"
            for d in debits[:3]:
                response += f"• -₹{d['amount']} ({d['created_at'][:10]})\n"
        
        bot.reply_to(message, response, parse_mode='HTML')
        
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['chk_order'])
def chk_order_command(message):
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Admin access required")
        return
    
    # Check if in correct topic
    if message.chat.id != ADMIN_LOGS_GROUP or message.message_thread_id != ADMIN_LOGS_TOPIC['admin_funds_add']:
        bot.reply_to(message, "❌ This command only works in admin_funds_add topic")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "❌ Format: /chk_order <order_id>")
            return
        
        order_id = args[1]
        
        # Check in payments collection
        payment = payments_col.find_one({"payment_id": order_id})
        if payment:
            response = (
                f"🛒 <b>Payment Details</b>\n\n"
                f"📋 Order ID: {order_id}\n"
                f"👤 User ID: {payment['user_id']}\n"
                f"💰 Amount: ₹{payment['amount']}\n"
                f"📊 Status: {payment['status']}\n"
                f"💳 Method: {payment['method']}\n"
                f"📌 Txn ID: {payment.get('txn_id', 'N/A')}\n"
                f"📅 Created: {payment['created_at']}"
            )
            bot.reply_to(message, response, parse_mode='HTML')
            return
        
        # Check in history collection (debit transactions)
        history = history_col.find_one({"order_id": order_id})
        if history:
            response = (
                f"🛒 <b>Transaction Details</b>\n\n"
                f"📋 Order ID: {order_id}\n"
                f"👤 User ID: {history['user_id']}\n"
                f"💰 Amount: ₹{history['amount']}\n"
                f"📊 Type: {history['type']}\n"
                f"📦 Service: {history.get('service', 'N/A')}\n"
                f"📅 Created: {history['created_at']}"
            )
            bot.reply_to(message, response, parse_mode='HTML')
            return
        
        bot.reply_to(message, f"❌ Order not found: {order_id}")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Admin access required")
        return
    
    # Check if in correct topic
    if message.chat.id != ADMIN_LOGS_GROUP or message.message_thread_id != ADMIN_LOGS_TOPIC['admin_funds_add']:
        bot.reply_to(message, "❌ This command only works in admin_funds_add topic")
        return
    
    msg = bot.reply_to(
        message,
        "📢 Send the broadcast message (text/photo/video):"
    )
    bot.register_next_step_handler(msg, process_broadcast)

def process_broadcast(message):
    admin_id = message.from_user.id
    users = get_all_wallet_users()
    
    if not users:
        bot.reply_to(message, "❌ No users found")
        return
    
    bot.reply_to(message, f"🚀 Broadcasting to {len(users)} users...")
    
    success = 0
    failed = 0
    
    for user_data in users:
        try:
            user_id = user_data['user_id']
            
            if message.content_type == 'text':
                bot.send_message(user_id, message.text, parse_mode='HTML')
            elif message.content_type == 'photo':
                bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption, parse_mode='HTML')
            elif message.content_type == 'video':
                bot.send_video(user_id, message.video.file_id, caption=message.caption, parse_mode='HTML')
            
            success += 1
        except:
            failed += 1
        
        time.sleep(0.05)  # Rate limit
    
    bot.reply_to(message, f"✅ Broadcast complete!\nSuccess: {success}\nFailed: {failed}")

# ================== START COMMAND ==================
@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    
    # Save or update user
    user_data = message.from_user
    update_wallet_user(user_id, {
        "username": user_data.username,
        "first_name": user_data.first_name,
        "joined_at": get_ist_time().isoformat(),
        "is_banned": False
    })
    
    # Check if banned
    user = get_wallet_user(user_id)
    if user and user.get('is_banned'):
        bot.reply_to(message, "🚫 You are banned from using this bot.")
        return
    
    # Welcome message
    welcome_text = (
        "👋 Welcome to NexGen Wallet Bot\n"
        "💎 Official Wallet of NexGen Deals\n\n"
        "🪙 Buy & Manage NexGen Coin\n"
        "⚡ Instant Wallet System\n"
        "🔒 100% Secure Payments\n\n"
        "Tap below to continue 👇"
    )
    
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🛒 Start Payment", callback_data="start_payment")
    )
    
    bot.send_message(user_id, welcome_text, reply_markup=markup)
    
    # Send keyboard
    send_main_keyboard(user_id)

def send_main_keyboard(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🛒 Start Payment")
    markup.add("💰 Wallet Balance", "📝 Transaction")
    markup.add("📃 Rules", "📞 Contact Team")
    
    try:
        bot.send_message(user_id, "Choose an option:", reply_markup=markup)
    except:
        pass

# ================== MESSAGE HANDLERS ==================
@bot.message_handler(func=lambda message: message.text == "🛒 Start Payment")
def handle_start_payment_button(message):
    user_id = message.from_user.id
    
    # Check if user has pending payment
    if str(user_id) in active_qrs:
        bot.reply_to(
            message,
            "❌ You already have a pending payment.\n"
            "Please complete or cancel it first."
        )
        return
    
    payment_text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "🪙 NexGen Coin Purchase\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "💱 Conversion Rate:\n"
        "1 NexGen Coin = ₹1 INR\n\n"
        "Minimum Purchase: 1 Coins\n"
        "Maximum Purchase: 1,00,000 Coins\n\n"
        "Your coins will be credited instantly\n"
        "after successful payment.\n\n"
        "Click Generate QR Button and Pay Any Amount."
    )
    
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🛒 Generate QR", callback_data="generate_qr")
    )
    
    bot.send_message(user_id, payment_text, reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "💰 Wallet Balance")
def handle_wallet_balance(message):
    user_id = message.from_user.id
    wallet = get_wallet(user_id)
    
    balance = wallet.get('balance', 0) if wallet else 0
    total_deposit = wallet.get('total_deposit', 0) if wallet else 0
    total_spent = wallet.get('total_spent', 0) if wallet else 0
    
    balance_text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "💼 Your NexGen Wallet Balance\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 User ID: {user_id}\n\n"
        f"💰 Available Balance : ₹{balance} INR\n"
        f"🪙 NexGen Coin Value : {balance} NexGen Coins\n\n"
        f"💱 Conversion Rate:\n"
        f"1 NexGen Coin = ₹1 INR\n\n"
        f"Spent balance : ₹{total_spent}\n"
        f"Total Balance Added: ₹{total_deposit}\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "You can use this balance to purchase\n"
        "services on NexGen Official Bots only.\n"
        "━━━━━━━━━━━━━━━━━━━"
    )
    
    bot.reply_to(message, balance_text)

@bot.message_handler(func=lambda message: message.text == "📝 Transaction")
def handle_transaction(message):
    user_id = message.from_user.id
    
    # Get last 10 transactions
    transactions = list(history_col.find(
        {"user_id": user_id}
    ).sort("created_at", -1).limit(10))
    
    if not transactions:
        bot.reply_to(message, "📭 No transactions found.")
        return
    
    trans_text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "📝 Your NexGen Transaction History\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    for t in transactions:
        try:
            time_str = t['created_at'][:16].replace('T', ' | ')
            
            if t['type'] == 'credit':
                trans_text += (
                    f"🟢 Credit: ₹{t['amount']}\n"
                    f"NexPay Id: {t.get('payment_id', 'N/A')}\n"
                    f"🕒 {time_str}\n\n"
                )
            else:
                trans_text += (
                    f"🔴 Debit: ₹{t['amount']}\n"
                    f"NexPay Id: {t.get('order_id', 'N/A')}\n"
                    f"🕒 {time_str}\n\n"
                )
        except:
            continue
    
    bot.reply_to(message, trans_text[:4000])  # Telegram message limit

@bot.message_handler(func=lambda message: message.text == "📃 Rules")
def handle_rules(message):
    rules_text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "📃 NexGen Wallet Rules\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ All payments are final. No refund policy.\n\n"
        "2️⃣ Fake payment / Fake screenshot = Permanent Ban 🚫\n\n"
        "3️⃣ Aap jo payment karoge vo refund nahi hoga.\n"
        "   Uska use aapko hamare bots par hi karna hoga.\n\n"
        "4️⃣ Wallet balance ko sirf NexGen official bots par use kiya ja sakta hai.\n\n"
        "5️⃣ Wallet balance refundable nahi hai.\n\n"
        "6️⃣ Rules admin policy ke hisab se honge.\n\n"
        "7️⃣ Any misuse / fraud activity = Account suspension.\n\n"
        "8️⃣ Respect Support Team & Follow instructions.\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Using this wallet means you agree to all above rules."
    )
    
    bot.reply_to(message, rules_text)

@bot.message_handler(func=lambda message: message.text == "📞 Contact Team")
def handle_contact(message):
    contact_text = (
        "📞 Need Help?\n\n"
        "Contact Our Support Team:\n"
        "@NexGenSupport"
    )
    
    bot.reply_to(message, contact_text)

# ================== CALLBACK HANDLERS ==================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    data = call.data
    
    if data == "start_payment":
        # Delete previous message
        try:
            bot.delete_message(user_id, call.message.message_id)
        except:
            pass
        
        # Check if user has pending payment
        if str(user_id) in active_qrs:
            bot.answer_callback_query(call.id, "❌ You already have a pending payment")
            return
        
        payment_text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "🪙 NexGen Coin Purchase\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "💱 Conversion Rate:\n"
            "1 NexGen Coin = ₹1 INR\n\n"
            "Minimum Purchase: 1 Coins\n"
            "Maximum Purchase: 1,00,000 Coins\n\n"
            "Your coins will be credited instantly\n"
            "after successful payment.\n\n"
            "Click Generate QR Button and Pay Any Amount."
        )
        
        markup = InlineKeyboardMarkup().add(
            InlineKeyboardButton("🛒 Generate QR", callback_data="generate_qr")
        )
        
        bot.send_message(user_id, payment_text, reply_markup=markup)
        bot.answer_callback_query(call.id)
        
    elif data == "generate_qr":
        # Check if user has pending payment
        if str(user_id) in active_qrs:
            bot.answer_callback_query(call.id, "❌ You already have a pending payment")
            return
        
        # Generate order ID
        order_id = generate_order_id(user_id)
        
        # Generate QR
        qr_img = generate_upi_qr(order_id)
        
        caption = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "🪙 NexGen Coin Purchase\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 Order ID: <code>{order_id}</code>\n\n"
            "Scan QR to pay ANY amount\n"
            "⏱ QR expires in 10 minutes\n\n"
            "After payment, coins will be\n"
            "credited automatically."
        )
        
        markup = InlineKeyboardMarkup().add(
            InlineKeyboardButton("❌ Cancel Payment", callback_data=f"cancel_{order_id}")
        )
        
        try:
            # Delete previous message
            bot.delete_message(user_id, call.message.message_id)
            
            # Send QR
            msg = bot.send_photo(
                user_id,
                qr_img,
                caption=caption,
                reply_markup=markup,
                parse_mode='HTML'
            )
            
            # Track active QR
            active_qrs[str(user_id)] = {
                'qr_message_id': msg.message_id,
                'order_id': order_id
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
            
            bot.answer_callback_query(call.id, "✅ QR Generated")
            
        except Exception as e:
            print(f"Error sending QR: {e}")
            bot.answer_callback_query(call.id, "❌ Error generating QR")
    
    elif data.startswith("cancel_"):
        order_id = data.split("_", 1)[1]
        
        if str(user_id) in active_qrs and active_qrs[str(user_id)]['order_id'] == order_id:
            handle_cancelled_payment(user_id, order_id, active_qrs[str(user_id)]['qr_message_id'])
            bot.answer_callback_query(call.id, "❌ Payment cancelled")
        else:
            bot.answer_callback_query(call.id, "❌ No active payment found")

# ================== MAIN ==================
if __name__ == "__main__":
    # Start daily summary thread
    threading.Thread(target=daily_summary_task, daemon=True).start()
    
    print("NexGen Wallet Bot Started...")
    bot.infinity_polling()