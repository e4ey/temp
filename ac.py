import os
import json
import re
import time
import requests
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from requests.exceptions import RequestException, ConnectionError
from http.client import RemoteDisconnected
from telegram import Update, Document, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from colorama import init, Fore
from user_activity_tracker import track_user_activity  # Import the tracker

# Initialize colorama
init(autoreset=True)

# Telegram Bot Token
TELEGRAM_TOKEN = '7484832870:AAHWi9pcPfm0PEl8kaiJNubgJ9W-jhb-WFk'

# Path to keys file
KEYS_FILE_PATH = "keys.json"

# Global Variables
num_threads = 5
max_retries = 3
lock = Lock()

# Global variables for extra membership functionality
extra_memberships = 0
extra_members = False
plan = None
email = None
duplicate_cookies = None
ID = None
info = None
country = None
membership_status = None  # Added

def read_keys():
    if os.path.exists(KEYS_FILE_PATH):
        with open(KEYS_FILE_PATH, 'r') as file:
            return json.load(file)
    return {"authorized_keys": [], "user_keys": {}}

async def start(update: Update, context: CallbackContext) -> None:
    track_user_activity(update, "Started Bot")  # Track user activity
    if not context.chat_data.get('authenticated', False):
        await update.message.reply_text(
            "🔑 Please provide the authentication key to access the bot.\n"
            "Use the command `/auth <your_key>` to authenticate."
        )
        return

    welcome_message = (
        "👋 Welcome to the Netflix Cookie Checker Bot!\n\n"
        "📄 Send me your Netflix cookies in JSON format, and I'll check which ones are working.\n"
        "📤 You can upload multiple files at once.\n"
        "⚠️ Make sure your cookies are correctly formatted.\n"
        "🔒 Your data is handled securely and not stored after processing."
    )
    await update.message.reply_text(welcome_message)

async def auth(update: Update, context: CallbackContext) -> None:
    track_user_activity(update, "Attempted Authentication")  # Track user activity
    user_id = update.effective_user.id
    if len(context.args) != 1:
        await update.message.reply_text("⚠️ Please provide a single authentication key.")
        return

    provided_key = context.args[0]
    data = read_keys()
    
    if provided_key in data["authorized_keys"]:
        context.chat_data['authenticated'] = True
        data["user_keys"][user_id] = provided_key
        with open(KEYS_FILE_PATH, 'w') as file:
            json.dump(data, file, indent=4)
        track_user_activity(update, "Authenticated Successfully")  # Track success
        await update.message.reply_text("✅ You have been authenticated successfully. You can now use the bot.")
    else:
        track_user_activity(update, "Failed Authentication")  # Track failure
        await update.message.reply_text("❌ Invalid key. Please check and try again.")

async def help_command(update: Update, context: CallbackContext) -> None:
    track_user_activity(update, "Requested Help")  # Track user activity
    if not context.chat_data.get('authenticated', False):
        await update.message.reply_text("⚠️ You need to authenticate first using the `/auth <your_key>` command.")
        return

    help_message = (
        "❓ **How to Use the Netflix Cookie Checker Bot:**\n\n"
        "1. Prepare your Netflix cookies in **JSON format**.\n"
        "2. Send the cookie files directly to this chat.\n"
        "3. Wait for the bot to process the cookies.\n"
        "4. Receive a summary of the results and the working cookies.\n\n"
        "**Note:** Your data is processed securely and not stored."
    )
    await update.message.reply_text(help_message, parse_mode='Markdown')

def identify_file(file_name):
    try:
        with open(file_name, "r") as file_content:
            json.load(file_content)
            return "json"
    except json.JSONDecodeError:
        return "netscape"
    except Exception as e:
        print(f"An error occurred while processing {file_name}: {str(e)}")
        return "error"

def convert_netscape_cookie_to_json(cookie_file_content):
    cookies = []
    for line in cookie_file_content.splitlines():
        fields = line.strip().split("\t")
        if len(fields) >= 7:
            cookie = {
                "domain": fields[0].replace("www", ""),
                "flag": fields[1],
                "path": fields[2],
                "secure": fields[3] == "TRUE",
                "expiration": fields[4],
                "name": fields[5],
                "value": fields[6],
            }
            cookies.append(cookie)
    return cookies

async def save_cookie_file(document: Document, user_id: int) -> str:
    os.makedirs(f"temp_cookies/{user_id}", exist_ok=True)
    file_path = os.path.join(f"temp_cookies/{user_id}", document.file_name)
    file = await document.get_file()
    await file.download_to_drive(custom_path=file_path)
    return file_path

async def load_cookies_from_json(json_cookies_path):
    with open(json_cookies_path, "r", encoding="utf-8") as cookie_file:
        cookies = json.load(cookie_file)
    return cookies

async def load_cookies_from_netscape(netscape_cookies_path):
    with open(netscape_cookies_path, "r", encoding="utf-8") as file:
        content = file.read()
    cookies = convert_netscape_cookie_to_json(content)
    return cookies

def extract_info(response_text):
    patterns = {
        "countryOfSignup": r'"countryOfSignup":\s*"([^"]+)"',
        "localizedPlanName": r'"localizedPlanName":\s*\{\s*"fieldType":\s*"String",\s*"value":\s*"([^"]+)"',
        "emailAddress": r'"emailAddress":\s*"([^"]+)"',  # Updated pattern
        "membershipStatus": r'"membershipStatus":\s*"([^"]+)"'  # Added pattern
    }
    extracted_info = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, response_text)
        extracted_info[key] = match.group(1) if match else "N/A"
    return extracted_info

async def open_webpage_with_cookies(session, url, cookies):
    global extra_memberships, extra_members, plan, email, duplicate_cookies, ID, info, country, membership_status

    session.cookies.clear()
    for cookie in cookies:
        session.cookies.set(cookie.get("name"), cookie.get("value"))
    session.headers.update({"Accept-Encoding": "identity", "User-Agent": "Mozilla/5.0"})

    attempts = 0
    while attempts < max_retries:
        try:
            response = session.get(url, timeout=10)
            if "Sign In" in response.text or "signin" in response.url:
                return False, {"error": "Cookie expired or invalid."}

            info = extract_info(response.text)
            soup = BeautifulSoup(response.text, "lxml")

            # Check for extra memberships
            response = session.get(
                "https://www.netflix.com/accountowner/addextramember",
                allow_redirects=False,
            )
            if response.status_code == 200:
                # Extra memberships page is accessible
                extra_memberships = 1
                extra_members = True
            else:
                extra_memberships = 0
                extra_members = False
                
            return True, info
        except (RequestException, ConnectionError, RemoteDisconnected) as e:
            attempts += 1
            time.sleep(2)
    return False, {"error": "Network error occurred during the request."}

async def process_cookie_file(file_path):
    result = {
        "file_name": os.path.basename(file_path),
        "status": "",
        "info": {},
        "extra_memberships": 0,
        "extra_members": False
    }
    try:
        file_type = identify_file(file_path)
        if file_type == "json":
            cookies = await load_cookies_from_json(file_path)
        elif file_type == "netscape":
            cookies = await load_cookies_from_netscape(file_path)
        else:
            result["status"] = "error"
            return result

        with requests.Session() as session:
            success, info = await open_webpage_with_cookies(
                session, "https://www.netflix.com/YourAccount", cookies
            )
            if success:
                result["status"] = "success"
                result["info"] = info
                result["extra_memberships"] = extra_memberships
                result["extra_members"] = extra_members
            else:
                result["status"] = "error"
                result["info"] = info

    except Exception as e:
        result["status"] = "error"
        result["info"] = {"error": str(e)}

    return result

async def handle_file_upload(update: Update, context: CallbackContext) -> None:
    track_user_activity(update, "Uploaded Cookie File")  # Track user activity
    if not context.chat_data.get('authenticated', False):
        await update.message.reply_text("⚠️ You need to authenticate first using the `/auth <your_key>` command.")
        return

    if not update.message.document:
        await update.message.reply_text("⚠️ Please send a valid file.")
        return

    document = update.message.document
    user_id = update.effective_user.id
    file_path = await save_cookie_file(document, user_id)

    result = await process_cookie_file(file_path)
    if result["status"] == "success":
        status_message = (
            f"✅ {result['file_name']} processed successfully!\n"
            f"🌍 Country: {result['info'].get('countryOfSignup', 'N/A')}\n"
            f"📧 Email: {result['info'].get('emailAddress', 'N/A')}\n"
            f"📜 Plan: {result['info'].get('localizedPlanName', 'N/A')}\n"
            f"🏷️ Membership Status: {result['info'].get('membershipStatus', 'N/A')}\n"
            f"👥 Extra Memberships: {'Yes' if result['extra_members'] else 'No'}\n"
        )

        await update.message.reply_text(status_message)
    else:
        await update.message.reply_text(f"❌ Failed to process {result['file_name']}: {result['info'].get('error', 'Unknown error')}")

    # Clean up temporary files
    try:
        os.remove(file_path)
    except Exception as e:
        print(f"Error removing file {file_path}: {e}")

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("auth", auth))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_file_upload))

    application.run_polling()

if __name__ == '__main__':
    main()
