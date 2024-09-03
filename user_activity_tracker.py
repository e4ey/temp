import logging
from datetime import datetime
from telegram import Update

# Set up logging configuration
logging.basicConfig(
    filename='user_activity.log',
    level=logging.INFO,
    format='%(asctime)s - USER: %(username)s (ID: %(user_id)s) - ACTION: %(action)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def log_user_activity(user_id: int, username: str, action: str):
    logging.info('', extra={'user_id': user_id, 'username': username, 'action': action})

def track_user_activity(update: Update, action: str):
    user = update.effective_user
    user_id = user.id
    username = user.username or "Unknown"
    log_user_activity(user_id, username, action)
