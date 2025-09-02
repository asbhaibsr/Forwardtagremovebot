import logging
import os
import datetime
from datetime import timedelta
import asyncio
from pymongo import MongoClient
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    ChatMemberUpdated,
)
from pyrogram.enums import ChatType
from pyrogram.errors import UserIsBlocked, RPCError, FloodWait

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Environment Variables (REQUIRED) ---
try:
    API_ID = int(os.environ.get('API_ID'))
    API_HASH = os.environ.get('API_HASH')
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    MONGO_URI = os.environ.get('MONGO_URI')
    ADMIN_ID = int(os.environ.get('ADMIN_ID'))
    LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID'))
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME')
    if not all([API_ID, API_HASH, BOT_TOKEN, MONGO_URI, ADMIN_ID, LOG_CHANNEL_ID, ADMIN_USERNAME]):
        raise ValueError("One or more required environment variables are missing.")
except (ValueError, TypeError) as e:
    logger.error(f"Environment variables not set correctly: {e}")
    exit(1)

# Connect to MongoDB
try:
    db_client = MongoClient(MONGO_URI)
    db = db_client.get_database('bot_database')
    users_collection = db.get_collection('users')
    channels_collection = db.get_collection('channels')
    premium_users_collection = db.get_collection('premium_users')
    user_channels_collection = db.get_collection('user_channels')
    logger.info("Connected to MongoDB successfully.")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    exit(1)

# Initialize Pyrogram Client
app = Client(
    "forward_tag_remover",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins={"root": "plugins"},
)

# --- Helper Functions ---
async def log_event(client, log_message: str):
    """Sends a log message to the log channel."""
    try:
        await client.send_message(chat_id=LOG_CHANNEL_ID, text=log_message)
    except Exception as e:
        logger.error(f"Failed to send log message to channel {LOG_CHANNEL_ID}: {e}")

async def is_user_premium(user_id: int) -> bool:
    """Checks if a user has an active premium subscription."""
    premium_user = premium_users_collection.find_one({'user_id': user_id})
    if premium_user and premium_user['expiry_date'] > datetime.datetime.now():
        return True
    return False

# --- Bot Commands and Handlers ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    user = message.from_user
    user_doc = {
        'user_id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'joined': datetime.datetime.now()
    }
    users_collection.update_one({'user_id': user.id}, {'$set': user_doc}, upsert=True)
    
    log_message = (
        f"**New User Started Bot!** 👤\n"
        f"User ID: `{user.id}`\n"
        f"Username: @{user.username}\n"
        f"Name: {user.first_name}\n"
        f"Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await log_event(client, log_message)
    logger.info(f"User started the bot: {user.id}")

    # You can add the channel verification logic here if needed, or remove it since the new bot can handle all channels.
    main_keyboard = [
        [InlineKeyboardButton("➕ Add Me to Your Channel", url=f"https://t.me/{client.me.username}?startgroup=start")],
        [InlineKeyboardButton("❓ Help", callback_data='help')],
        [InlineKeyboardButton("👑 Buy Premium", callback_data='buy_premium')]
    ]
    await message.reply_text(
        f"Hi {user.first_name}! Welcome back. This bot will remove forward tags from messages in any channel you add it to.",
        reply_markup=InlineKeyboardMarkup(main_keyboard)
    )

@app.on_message(filters.command("addchannel") & filters.private)
async def addchannel_command(client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        await message.reply_text("Please provide a channel ID. Usage: `/addchannel <channel_id>`")
        return
    
    try:
        channel_id = int(message.command[1])
    except (ValueError, IndexError):
        await message.reply_text("Invalid channel ID. Please provide a valid numerical ID.")
        return

    # Check if the user is a member of the channel
    try:
        member = await client.get_chat_member(channel_id, user_id)
        if member.status not in ["administrator", "creator"]:
            await message.reply_text("You must be an admin of the channel to add it.")
            return
    except RPCError as e:
        await message.reply_text(f"Could not find the channel or verify membership. Error: {e}")
        return

    is_premium = await is_user_premium(user_id)
    if not is_premium:
        channel_count = user_channels_collection.count_documents({'user_id': user_id})
        if channel_count >= 2:
            await message.reply_text("You have already added 2 free channels. To add more, please buy premium.")
            return

    user_channel_doc = {
        'user_id': user_id,
        'channel_id': channel_id,
        'added_by_user': user_id,
        'is_premium': is_premium,
        'added_date': datetime.datetime.now()
    }
    user_channels_collection.update_one(
        {'user_id': user_id, 'channel_id': channel_id},
        {'$set': user_channel_doc},
        upsert=True
    )

    channel_info = await client.get_chat(channel_id)
    
    channel_doc = {
        'channel_id': channel_id,
        'title': channel_info.title,
        'type': channel_info.type.value,
        'joined': datetime.datetime.now(),
        'added_by_user': user_id
    }
    channels_collection.update_one(
        {'channel_id': channel_id},
        {'$set': channel_doc},
        upsert=True
    )
    
    if is_premium:
        await message.reply_text(f"✅ Channel `{channel_info.title}` has been successfully added to your premium account. You can add unlimited channels.")
    else:
        await message.reply_text(f"✅ Channel `{channel_info.title}` has been successfully added. You can add one more free channel.")
        
    await client.send_message(chat_id=channel_id, text="This channel has been successfully connected by its admin. I will now remove forwarded tags from messages.")


@app.on_message(filters.command("removechannel") & filters.private)
async def removechannel_command(client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        await message.reply_text("Please provide a channel ID. Usage: `/removechannel <channel_id>`")
        return
    
    try:
        channel_id = int(message.command[1])
    except (ValueError, IndexError):
        await message.reply_text("Invalid channel ID. Please provide a valid numerical ID.")
        return

    result = user_channels_collection.delete_one({'user_id': user_id, 'channel_id': channel_id})
    if result.deleted_count > 0:
        await message.reply_text(f"✅ Channel `{channel_id}` has been successfully removed from your account.")
    else:
        await message.reply_text(f"Channel `{channel_id}` was not found in your list of added channels.")


@app.on_message(filters.command("add_premium") & filters.private)
async def add_premium_command(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("You are not authorized to use this command.")
        return
    try:
        args = message.command
        premium_user_id = int(args[1])
        premium_duration = 365 # 1 year
        expiry_date = datetime.datetime.now() + timedelta(days=premium_duration)
        
        user_doc = users_collection.find_one({'user_id': premium_user_id})
        if not user_doc:
            await message.reply_text(f"User with ID `{premium_user_id}` not found in the database.")
            return

        premium_users_collection.update_one(
            {'user_id': premium_user_id},
            {'$set': {'expiry_date': expiry_date, 'added_by_admin': ADMIN_ID}},
            upsert=True
        )
        
        await message.reply_text(f"User `{premium_user_id}` has been granted premium for 1 year.")
        
        try:
            await client.send_message(chat_id=premium_user_id, text="🥳 Your premium subscription has been activated! You can now add the bot to unlimited channels.")
        except Exception:
            logger.warning(f"Failed to send a message to the user {premium_user_id} about premium activation.")

    except (IndexError, ValueError):
        await message.reply_text("Usage: /add_premium <user_id>")

@app.on_message(filters.command("remove_premium") & filters.private)
async def remove_premium_command(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("You are not authorized to use this command.")
        return
    try:
        args = message.command
        premium_user_id = int(args[1])
        result = premium_users_collection.delete_one({'user_id': premium_user_id})
        if result.deleted_count > 0:
            await message.reply_text(f"Premium status for user `{premium_user_id}` has been removed successfully.")
        else:
            await message.reply_text(f"User `{premium_user_id}` does not have an active premium subscription.")
    except (IndexError, ValueError):
        await message.reply_text("Usage: /remove_premium <user_id>")

@app.on_message(filters.command("stats") & filters.private)
async def stats_command(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("You are not authorized to use this command.")
        return
    user_count = users_collection.count_documents({})
    channel_count = channels_collection.count_documents({})
    premium_count = premium_users_collection.count_documents({'expiry_date': {'$gt': datetime.datetime.now()}})
    stats_text = (
        f"📊 **Bot Stats**\n\n"
        f"Total Users: {user_count}\n"
        f"Total Channels Bot is in: {channel_count}\n"
        f"Premium Users: {premium_count}"
    )
    await message.reply_text(stats_text, parse_mode='Markdown')

@app.on_message(filters.command("premium_stats") & filters.private)
async def premium_stats_command(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("You are not authorized to use this command.")
        return
    premium_users = premium_users_collection.find({})
    premium_list = list(premium_users)
    if not premium_list:
        await message.reply_text("No premium users found.")
        return
    stats_text = "👑 **Premium User Stats**\n\n"
    for user in premium_list:
        user_id = user['user_id']
        expiry_date = user['expiry_date']
        status = "✅ Active" if expiry_date > datetime.datetime.now() else "❌ Expired"
        stats_text += f"**User ID:** `{user_id}`\n"
        stats_text += f"**Status:** {status}\n"
        stats_text += f"**Expiry Date:** {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
        stats_text += "-------------------------\n"
    await message.reply_text(stats_text, parse_mode='Markdown')

@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("You are not authorized to use this command.")
        return
    if not message.reply_to_message:
        await message.reply_text("Please reply to a message you want to broadcast to users.")
        return
    
    message_to_broadcast = message.reply_to_message
    users = users_collection.find({})
    sent_count = 0
    blocked_count = 0
    for user_doc in users:
        try:
            await client.copy_message(
                chat_id=user_doc['user_id'],
                from_chat_id=message_to_broadcast.chat.id,
                message_id=message_to_broadcast.id
            )
            sent_count += 1
            await asyncio.sleep(0.1) # Add a small delay
        except UserIsBlocked:
            blocked_count += 1
        except Exception:
            blocked_count += 1
    await message.reply_text(f"User Broadcast complete. Sent to {sent_count} users. Blocked by {blocked_count} users.")


@app.on_message(filters.command("channel_broadcast") & filters.private)
async def channel_broadcast_command(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("You are not authorized to use this command.")
        return
    if not message.reply_to_message:
        await message.reply_text("Please reply to a message you want to broadcast to channels.")
        return
    
    message_to_broadcast = message.reply_to_message
    channels = channels_collection.find({})
    sent_count = 0
    failed_count = 0
    for channel_doc in channels:
        channel_id = channel_doc['channel_id']
        try:
            await client.copy_message(
                chat_id=channel_id,
                from_chat_id=message_to_broadcast.chat.id,
                message_id=message_to_broadcast.id
            )
            sent_count += 1
            await asyncio.sleep(0.1) # Add a small delay
        except Exception:
            failed_count += 1
    await message.reply_text(f"Channel Broadcast complete. Sent to {sent_count} channels. Failed on {failed_count} channels.")


@app.on_message(filters.private & filters.regex("^(help|buy_premium|back_to_start)$"))
async def handle_callback_message(client, message: Message):
    # This is a fallback in case a user sends the callback data as a message.
    # The actual logic is in the inline query handler.
    pass

@app.on_callback_query()
async def callback_handler(client, query):
    data = query.data
    user_id = query.from_user.id
    await query.answer()

    if data == 'help':
        help_text = (
            "**❓ Help & Support**\n\n"
            "**Forwarding:**\n"
            "Just add me to your channel. Then, use the `/addchannel` command in our private chat with me, providing the channel ID. I will automatically remove the 'Forwarded from' tag. This is a free feature, and you can add up to **two channels** for free.\n\n"
            "**Premium:**\n"
            "To get premium features and add me to unlimited channels, click the '👑 Buy Premium' button and follow the instructions. For any further assistance, you can contact the admin.\n\n"
            "**How to add me to your channel:**\n"
            "1. Click the 'Add Me to Your Channel' button.\n"
            "2. Select the channel where you want to add the bot.\n"
            "3. Use the `/addchannel` command with the channel ID in our private chat.\n"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back_to_start')]]
        await query.edit_message_text(text=help_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == 'buy_premium':
        premium_text = (
            "👑 **Buy Premium Service**\n\n"
            "Our premium service allows you to add the bot to unlimited channels for **₹300 for 1 year**.\n\n"
            "**How to Pay:**\n"
            "1. Pay the amount via UPI to this ID: `arsadsaifi8272@ibl`\n"
            "2. Take a screenshot of the payment.\n"
            "3. Click the button below to send the screenshot to our admin for verification."
        )
        keyboard = [
            [InlineKeyboardButton("💳 Send Screenshot", url=f"https://t.me/{ADMIN_USERNAME}")],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_start')]
        ]
        await query.edit_message_text(text=premium_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == 'back_to_start':
        user = query.from_user
        main_keyboard = [
            [InlineKeyboardButton("➕ Add Me to Your Channel", url=f"https://t.me/{client.me.username}?startgroup=start")],
            [InlineKeyboardButton("❓ Help", callback_data='help')],
            [InlineKeyboardButton("👑 Buy Premium", callback_data='buy_premium')]
        ]
        await query.edit_message_text(f"Hi {user.first_name}! Welcome back.", reply_markup=InlineKeyboardMarkup(main_keyboard))

# Pyrogram forward tag removal logic
@app.on_message(filters.forwarded & filters.chat(lambda _, __, m: user_channels_collection.find_one({'channel_id': m.chat.id})))
async def handle_forwarded_messages(client, message: Message):
    try:
        if message.forward_from or message.forward_from_chat:
            # Copy the message without the forward tag
            await message.copy(chat_id=message.chat.id)
            # Delete the original message with the forward tag
            await message.delete()
            logger.info(f"Forwarded message removed and resent in channel: {message.chat.title} ({message.chat.id})")
    except Exception as e:
        logger.error(f"Failed to handle forwarded message in channel {message.chat.id}: {e}")
        log_message = f"**ERROR:** An unexpected error occurred in channel `{message.chat.title}` (`{message.chat.id}`): `{e}`"
        await log_event(client, log_message)

def main():
    logger.info("Starting bot...")
    app.run()

if __name__ == "__main__":
    main()

