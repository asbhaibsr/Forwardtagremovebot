import logging
import datetime
from datetime import timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
import motor.motor_asyncio
import os

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

# --- Environment Variables (REQUIRED) ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
try:
    MAIN_CHANNEL_ID = int(os.environ.get('CHANNEL_ID'))
    ADMIN_ID = int(os.environ.get('ADMIN_ID'))
    LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID'))
except (ValueError, TypeError):
    logging.error("Environment variables for IDs are not set correctly. Please check Render dashboard.")
    exit(1) # Exit the application if critical variables are missing or invalid

MONGO_URI = os.environ.get('MONGO_URI')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME')

# --- Webhook Variables (REQUIRED FOR RENDER) ---
PORT = int(os.environ.get('PORT', 5000))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')

# Connect to MongoDB
db_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = db_client.get_database('bot_database')
users_collection = db.get_collection('users')
channels_collection = db.get_collection('channels')
premium_channels_collection = db.get_collection('premium_channels')

# --- Helper Functions ---
async def is_user_in_channel(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=MAIN_CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

# --- Bot Commands and Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if update.effective_chat.type != 'private':
        return

    existing_user = await users_collection.find_one({'user_id': user.id})
    if not existing_user:
        log_message = (
            f"**New User Started Bot!** 👤\n"
            f"User ID: `{user.id}`\n"
            f"Username: @{user.username}\n"
            f"First Name: {user.first_name}"
        )
        try:
            await context.bot.send_message(LOG_CHANNEL_ID, text=log_message, parse_mode='Markdown')
        except Exception as e:
            logging.error(f"Failed to send log to channel: {e}")

    await users_collection.update_one(
        {'user_id': user.id},
        {'$set': {'username': user.username, 'first_name': user.first_name}},
        upsert=True
    )
    logging.info(f"User started the bot: {user.id}")

    keyboard = [
        [InlineKeyboardButton("➕ Add Me to Your Channel", url=f"https://t.me/{context.bot.username}?startgroup=start")],
        [InlineKeyboardButton("❓ Help", callback_data='help')],
        [InlineKeyboardButton("👑 Buy Premium", callback_data='buy_premium')]
    ]

    if not await is_user_in_channel(user.id, context):
        keyboard[0][0] = InlineKeyboardButton("Join Our Channel", url=f"https://t.me/{MAIN_CHANNEL_ID}")
        keyboard.insert(1, [InlineKeyboardButton("✅ Verify", callback_data='verify_join')])
        await update.message.reply_text(
            'Please join our channel to use this bot.',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            f'Hi {user.first_name}! Welcome back.',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if await is_user_in_channel(user_id, context):
        await query.edit_message_text('✅ You have joined the channel. You can now use the bot.')
    else:
        keyboard = [
            [InlineKeyboardButton("Join Channel", url=f"https://t.me/{MAIN_CHANNEL_ID}")],
            [InlineKeyboardButton("✅ Verify", callback_data='verify_join')]
        ]
        await query.edit_message_text(
            'You haven\'t joined the channel yet. Please join and try again.',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# --- Premium System Handlers ---
async def buy_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    premium_text = (
        "👑 **Buy Premium Service**\n\n"
        "Here are our premium plans:\n"
        "1.  **1 Month:** ₹100\n"
        "2.  **5 Months:** ₹500\n\n"
        "**How to Pay:**\n"
        "1.  Pay the amount via UPI to this ID: `arsadsaifi8272@ibl`\n"
        "2.  Take a screenshot of the payment.\n"
        "3.  Click the button below to send the screenshot to our admin."
    )
    
    keyboard = [[InlineKeyboardButton("💳 Send Screenshot", url=f"https://t.me/{ADMIN_USERNAME}")]]
    
    await query.edit_message_text(
        text=premium_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def add_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text('You are not authorized to use this command.')
        return

    try:
        args = context.args
        channel_id = int(args[0])
        duration_str = args[1].lower()
        
        duration = 0
        if 'month' in duration_str:
            duration = int(duration_str.replace('month', ''))
        else:
            await update.message.reply_text('Invalid duration format. Use like: `1month`, `2month`.')
            return

        expiry_date = datetime.datetime.now() + timedelta(days=30 * duration)
        
        await premium_channels_collection.update_one(
            {'channel_id': channel_id},
            {'$set': {'expiry_date': expiry_date, 'added_by_admin': ADMIN_ID}},
            upsert=True
        )
        
        await update.message.reply_text(f'Channel {channel_id} has been added to premium for {duration} months.')
    except (IndexError, ValueError):
        await update.message.reply_text('Usage: `/add_premium <channel_id> <1month>`')

async def remove_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text('You are not authorized to use this command.')
        return

    try:
        args = context.args
        channel_id = int(args[0])
        
        result = await premium_channels_collection.delete_one({'channel_id': channel_id})
        
        if result.deleted_count > 0:
            await update.message.reply_text(f'Premium status for channel {channel_id} has been removed successfully.')
        else:
            await update.message.reply_text(f'Channel {channel_id} does not have an active premium subscription.')
    except (IndexError, ValueError):
        await update.message.reply_text('Usage: `/remove_premium <channel_id>`')

# --- Channel Handlers ---
async def handle_new_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channel_id = update.effective_chat.id
    message = update.channel_post
    
    # Forward tag removal is now a universal feature for all channels
    if message.forward_from or message.forward_from_chat:
        if message.text:
            await context.bot.send_message(channel_id, text=message.text, entities=message.entities)
        elif message.photo:
            await context.bot.send_photo(channel_id, photo=message.photo[-1].file_id, caption=message.caption, caption_entities=message.caption_entities)
        elif message.video:
            await context.bot.send_video(channel_id, video=message.video.file_id, caption=message.caption, caption_entities=message.caption_entities)
        elif message.document:
            await context.bot.send_document(channel_id, document=message.document.file_id, caption=message.caption, caption_entities=message.caption_entities)
        elif message.audio:
            await context.bot.send_audio(channel_id, audio=message.audio.file_id, caption=message.caption, caption_entities=message.caption_entities)
        elif message.voice:
            await context.bot.send_voice(channel_id, voice=message.voice.file_id, caption=message.caption, caption_entities=message.caption_entities)
        
        await message.delete()

async def on_bot_added_to_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type in ['channel', 'supergroup']:
        await channels_collection.update_one(
            {'channel_id': chat.id},
            {'$set': {'title': chat.title, 'type': chat.type}},
            upsert=True
        )
        logging.info(f"Bot added to new channel: {chat.title} ({chat.id})")
        
        log_message = (
            f"**Bot Added to New Channel!** 🎉\n"
            f"Channel ID: `{chat.id}`\n"
            f"Title: {chat.title}"
        )
        try:
            await context.bot.send_message(LOG_CHANNEL_ID, text=log_message, parse_mode='Markdown')
        except Exception as e:
            logging.error(f"Failed to send log to channel: {e}")

        await context.bot.send_message(
            chat_id=chat.id,
            text='Please buy premium to stop ads from appearing on your channel.',
        )

# --- Admin Commands ---
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text('You are not authorized to use this command.')
        return

    user_count = await users_collection.count_documents({})
    channel_count = await channels_collection.count_documents({})
    
    stats_text = (
        f'📊 **Bot Stats**\n\n'
        f'Total Users: {user_count}\n'
        f'Total Channels Bot is in: {channel_count}'
    )
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def premium_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text('You are not authorized to use this command.')
        return

    premium_channels = premium_channels_collection.find({})
    premium_list = await premium_channels.to_list(length=None)
    
    if not premium_list:
        await update.message.reply_text('No premium channels found.')
        return

    stats_text = '👑 **Premium Channel Stats**\n\n'
    for channel in premium_list:
        channel_id = channel['channel_id']
        expiry_date = channel['expiry_date']
        
        stats_text += f'**Channel ID:** `{channel_id}`\n'
        stats_text += f'**Expiry Date:** {expiry_date.strftime("%Y-%m-%d %H:%M:%S")}\n'
        stats_text += '-------------------------\n'
        
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text('You are not authorized to use this command.')
        return

    if not update.message.reply_to_message:
        await update.message.reply_text('Please reply to a message you want to broadcast to users.')
        return

    message_to_broadcast = update.message.reply_to_message
    
    users = users_collection.find({})
    sent_count = 0
    blocked_count = 0
    
    for user_doc in await users.to_list(length=None):
        try:
            await context.bot.copy_message(
                chat_id=user_doc['user_id'],
                from_chat_id=message_to_broadcast.chat.id,
                message_id=message_to_broadcast.message_id
            )
            sent_count += 1
        except Exception:
            blocked_count += 1
    
    await update.message.reply_text(f'User Broadcast complete. Sent to {sent_count} users. Blocked by {blocked_count} users.')

async def channel_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text('You are not authorized to use this command.')
        return

    if not update.message.reply_to_message:
        await update.message.reply_text('Please reply to a message you want to broadcast to channels.')
        return

    message_to_broadcast = update.message.reply_to_message
    
    channels = channels_collection.find({})
    sent_count = 0
    failed_count = 0
    
    for channel_doc in await channels.to_list(length=None):
        channel_id = channel_doc['channel_id']
        premium_channel = await premium_channels_collection.find_one({'channel_id': channel_id})
        
        # Check if the channel is NOT premium or if its premium has expired
        if not premium_channel or premium_channel['expiry_date'] < datetime.datetime.now():
            try:
                await context.bot.copy_message(
                    chat_id=channel_id,
                    from_chat_id=message_to_broadcast.chat.id,
                    message_id=message_to_broadcast.message_id
                )
                sent_count += 1
            except Exception:
                failed_count += 1
    
    await update.message.reply_text(f'Channel Broadcast complete. Sent to {sent_count} non-premium channels. Failed on {failed_count} channels.')

async def premium_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not context.args:
            await update.message.reply_text('Please provide a channel ID. Usage: `/premium_check <channel_id>`')
            return

        user_id = update.effective_user.id
        channel_id = int(context.args[0])

        # Check if the user is an admin of the specified channel
        try:
            member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status not in ['administrator', 'creator']:
                await update.message.reply_text('You are not an admin of this channel.')
                return
        except Exception:
            await update.message.reply_text('Could not find the channel or verify your admin status.')
            return

        # Check for premium status
        premium_channel = await premium_channels_collection.find_one({'channel_id': channel_id})

        if not premium_channel:
            await update.message.reply_text('This channel does not have a premium subscription.')
        else:
            expiry_date = premium_channel['expiry_date']
            if expiry_date > datetime.datetime.now():
                remaining_time = expiry_date - datetime.datetime.now()
                await update.message.reply_text(
                    f'✅ **Premium Subscription Active**\n\n'
                    f'Channel ID: `{channel_id}`\n'
                    f'Expiry Date: `{expiry_date.strftime("%Y-%m-%d %H:%M:%S")}`\n'
                    f'Remaining Time: `{remaining_time.days}` days'
                )
            else:
                await update.message.reply_text(
                    f'❌ **Premium Subscription Expired**\n\n'
                    f'Channel ID: `{channel_id}`\n'
                    f'Expiry Date: `{expiry_date.strftime("%Y-%m-%d %H:%M:%S")}`'
                )
    except (IndexError, ValueError):
        await update.message.reply_text('Invalid channel ID. Please provide a valid numerical ID. Usage: `/premium_check <channel_id>`')

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Public Handlers
    application.add_handler(CommandHandler('start', start_command, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(verify_callback, pattern='^verify_join$'))
    application.add_handler(CallbackQueryHandler(buy_premium_callback, pattern='^buy_premium$'))
    application.add_handler(CommandHandler('premium_check', premium_check_command))

    # Admin Handlers
    application.add_handler(CommandHandler('stats', stats_command))
    application.add_handler(CommandHandler('premium_stats', premium_stats_command))
    application.add_handler(CommandHandler('broadcast', broadcast_command))
    application.add_handler(CommandHandler('channel_broadcast', channel_broadcast_command))
    application.add_handler(CommandHandler('add_premium', add_premium_command))
    application.add_handler(CommandHandler('remove_premium', remove_premium_command))
    
    # General Handlers for channels and groups
    # THIS LINE WAS THE PROBLEM, NOW IT IS CORRECT.
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.FORWARDED, handle_new_posts), channel_post_updates=True)
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_bot_added_to_channel))
    
    # --- Webhook setup for Render deployment ---
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="",
        webhook_url=WEBHOOK_URL
    )

if __name__ == '__main__':
    main()
