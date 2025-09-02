import logging
import datetime
from datetime import timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)
import motor.motor_asyncio
import os
import asyncio

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- Environment Variables (REQUIRED) ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
try:
    MAIN_CHANNEL_ID = int(os.environ.get('CHANNEL_ID'))
    ADMIN_ID = int(os.environ.get('ADMIN_ID'))
    LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID'))
except (ValueError, TypeError):
    logging.error("Environment variables for IDs are not set correctly. Check LOG_CHANNEL_ID format ('-100xxxxxxxxxx').")
    exit(1)

MONGO_URI = os.environ.get('MONGO_URI')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME')
PORT = int(os.environ.get('PORT', 5000))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')

# Connect to MongoDB
db_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = db_client.get_database('bot_database')
users_collection = db.get_collection('users')
channels_collection = db.get_collection('channels')
premium_users_collection = db.get_collection('premium_users')
user_channels_collection = db.get_collection('user_channels')

# --- Helper Functions ---

async def log_event(context: ContextTypes.DEFAULT_TYPE, log_message: str) -> None:
    """Sends a log message to the log channel."""
    try:
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_message, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Failed to send log message to channel {LOG_CHANNEL_ID}: {e}")

async def is_user_in_channel(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=MAIN_CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

async def is_user_premium(user_id: int) -> bool:
    premium_user = await premium_users_collection.find_one({'user_id': user_id})
    if premium_user and premium_user['expiry_date'] > datetime.datetime.now():
        return True
    return False

# --- Bot Commands and Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    
    if update.effective_chat.type == 'private':
        user_doc = {
            'user_id': user.id,
            'username': user.username,
            'first_name': user.first_name,
            'joined': datetime.datetime.now()
        }
        await users_collection.update_one(
            {'user_id': user.id},
            {'$set': user_doc},
            upsert=True
        )
        log_message = (
            f"**New User Started Bot!** 👤\n"
            f"User ID: `{user.id}`\n"
            f"Username: @{user.username}\n"
            f"Name: {user.first_name}\n"
            f"Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await log_event(context, log_message)
        logging.info(f"User started the bot: {user.id}")

    if not await is_user_in_channel(user.id, context):
        join_keyboard = [
            [InlineKeyboardButton("Join Our Channel", url="https://t.me/Asbhai_bsr")],
            [InlineKeyboardButton("✅ Verify", callback_data='verify_join')],
        ]
        await update.message.reply_text(
            "Please join our channel to use this bot.",
            reply_markup=InlineKeyboardMarkup(join_keyboard)
        )
    else:
        main_keyboard = [
            [InlineKeyboardButton("➕ Add Me to Your Channel", url=f"https://t.me/{context.bot.username}?startchannel=start")],
            [InlineKeyboardButton("❓ Help", callback_data='help')],
            [InlineKeyboardButton("👑 Buy Premium", callback_data='buy_premium')]
        ]
        await update.message.reply_text(
            f"Hi {user.first_name}! Welcome back.",
            reply_markup=InlineKeyboardMarkup(main_keyboard)
        )

async def handle_all_messages_in_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    
    if message.forward_from or message.forward_from_chat:
        try:
            bot_member = await context.bot.get_chat_member(chat_id=message.chat.id, user_id=context.bot.id)
            if not bot_member.can_delete_messages:
                logging.warning(f"Bot cannot delete messages in channel {message.chat.id}. Forwarded tag will not be removed.")
                return

            text = message.text or message.caption
            entities = message.entities or message.caption_entities
            
            await message.delete()
            
            if message.photo:
                await context.bot.send_photo(
                    chat_id=message.chat.id,
                    photo=message.photo[-1].file_id,
                    caption=text,
                    caption_entities=entities
                )
            elif message.video:
                await context.bot.send_video(
                    chat_id=message.chat.id,
                    video=message.video.file_id,
                    caption=text,
                    caption_entities=entities
                )
            elif message.document:
                await context.bot.send_document(
                    chat_id=message.chat.id,
                    document=message.document.file_id,
                    caption=text,
                    caption_entities=entities
                )
            elif message.audio:
                await context.bot.send_audio(
                    chat_id=message.chat.id,
                    audio=message.audio.file_id,
                    caption=text,
                    caption_entities=entities
                )
            else:
                await context.bot.send_message(
                    chat_id=message.chat.id,
                    text=text,
                    entities=entities
                )

        except Exception as e:
            logging.error(f"Failed to handle forwarded message in channel {message.chat.id}: {e}")
            await log_event(context, f"**ERROR:** Failed to remove forwarded tag in channel `{message.chat.id}`: `{e}`")

async def remove_tags_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message.reply_to_message:
        await update.message.reply_text("Please reply to a forwarded message with this command to remove its forward tag.")
        return

    original_message = message.reply_to_message
    
    try:
        bot_member = await context.bot.get_chat_member(chat_id=original_message.chat.id, user_id=context.bot.id)
        if not bot_member.can_delete_messages:
            await update.message.reply_text("I cannot delete messages in this chat. Please grant me 'Delete messages' permission.")
            return

        text = original_message.text or original_message.caption
        entities = original_message.entities or original_message.caption_entities

        # Delete the original message with the forward tag
        await original_message.delete()
        await message.delete()

        # Send the message again without the forward tag
        if original_message.photo:
            await context.bot.send_photo(
                chat_id=original_message.chat.id,
                photo=original_message.photo[-1].file_id,
                caption=text,
                caption_entities=entities
            )
        elif original_message.video:
            await context.bot.send_video(
                chat_id=original_message.chat.id,
                video=original_message.video.file_id,
                caption=text,
                caption_entities=entities
            )
        elif original_message.document:
            await context.bot.send_document(
                chat_id=original_message.chat.id,
                document=original_message.document.file_id,
                caption=text,
                caption_entities=entities
            )
        elif original_message.audio:
            await context.bot.send_audio(
                chat_id=original_message.chat.id,
                audio=original_message.audio.file_id,
                caption=text,
                caption_entities=entities
            )
        else:
            await context.bot.send_message(
                chat_id=original_message.chat.id,
                text=text,
                entities=entities
            )
    except Exception as e:
        await update.message.reply_text(f"An error occurred while trying to remove the tag: {e}")
        logging.error(f"Failed to remove tag with command: {e}")

async def track_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    chat_member_update = update.chat_member
    
    if chat_member_update.new_chat_member.user.id == context.bot.id and \
       chat_member_update.new_chat_member.status in ['member', 'administrator', 'creator']:
        
        if chat.type in ['channel', 'supergroup', 'group']:
            channel_doc = {
                'channel_id': chat.id,
                'title': chat.title,
                'type': chat.type,
                'joined': datetime.datetime.now(),
            }
            await channels_collection.update_one(
                {'channel_id': chat.id},
                {'$set': channel_doc},
                upsert=True
            )
            logging.info(f"Bot added to new chat: {chat.title} ({chat.id})")
            log_message = (
                f"**Bot Added to New Channel!** 🎉\n"
                f"ID: `{chat.id}`\n"
                f"Title: {chat.title}\n"
                f"Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await log_event(context, log_message)

            await context.bot.send_message(
                chat_id=chat.id,
                text="Thank you for adding me! Please use the `/addchannel` command in a private chat with me to connect this channel to your account. Make sure I have 'Delete messages' rights to remove forwarded tags."
            )

async def addchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    try:
        channel_id = None
        if context.args and len(context.args) > 0:
            channel_id = int(context.args[0])
        elif update.message.reply_to_message:
            channel_id = update.message.reply_to_message.chat.id
        
        if not channel_id:
            await update.message.reply_text("Please provide a channel ID or reply to a message from the channel. Usage: `/addchannel <channel_id>`")
            return

        try:
            bot_member = await context.bot.get_chat_member(chat_id=channel_id, user_id=context.bot.id)
            if not bot_member.status in ['member', 'administrator', 'creator']:
                await update.message.reply_text("I am not a member of this channel. Please add me to the channel first.")
                return
            
            user_member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if not user_member.status in ['administrator', 'creator']:
                await update.message.reply_text("You are not an admin of this channel. Only channel admins can add their channels.")
                return

        except Exception as e:
            await update.message.reply_text(f"Could not find the channel or verify membership. Make sure I am in the channel and the ID is correct. Error: {e}")
            return

        is_premium = await is_user_premium(user_id)
        if not is_premium:
            existing_channel = await user_channels_collection.find_one({'user_id': user_id})
            if existing_channel:
                await update.message.reply_text("You have already added one channel. To add more, please buy premium.")
                return
        
        user_channel_doc = {
            'user_id': user_id,
            'channel_id': channel_id,
            'added_by_user': user_id,
            'is_premium': is_premium
        }
        await user_channels_collection.update_one(
            {'user_id': user_id},
            {'$set': user_channel_doc},
            upsert=True
        )

        channel_info = await context.bot.get_chat(channel_id)
        
        channel_doc = {
            'channel_id': channel_id,
            'title': channel_info.title,
            'type': channel_info.type,
            'joined': datetime.datetime.now(),
            'added_by_user': user_id
        }
        await channels_collection.update_one(
            {'channel_id': channel_id},
            {'$set': channel_doc},
            upsert=True
        )
        
        if is_premium:
            await update.message.reply_text(f"✅ Channel `{channel_info.title}` has been successfully added to your premium account. You can add unlimited channels.")
        else:
            await update.message.reply_text(f"✅ Channel `{channel_info.title}` has been successfully added. This is your one free channel. To add more channels, buy premium.")
            
        await context.bot.send_message(chat_id=channel_id, text=f"This channel has been successfully connected by its admin. I will now remove forwarded tags from messages. To remove a tag manually, please reply to a message with the `/remove_tags` command.")

    except (IndexError, ValueError):
        await update.message.reply_text("Invalid channel ID. Please provide a valid numerical ID or reply to a message from the channel. Usage: `/addchannel <channel_id>`")

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    if await is_user_in_channel(user_id, context):
        main_keyboard = [
            [InlineKeyboardButton("➕ Add Me to Your Channel", url=f"https://t.me/{context.bot.username}?startchannel=start")],
            [InlineKeyboardButton("❓ Help", callback_data='help')],
            [InlineKeyboardButton("👑 Buy Premium", callback_data='buy_premium')]
        ]
        await query.edit_message_text(
            "✅ You have joined the channel. You can now use the bot.",
            reply_markup=InlineKeyboardMarkup(main_keyboard)
        )
    else:
        keyboard = [
            [InlineKeyboardButton("Join Channel", url="https://t.me/Asbhai_bsr")],
            [InlineKeyboardButton("✅ Verify", callback_data='verify_join')],
        ]
        await query.edit_message_text(
            "You haven't joined the channel yet. Please join and try again.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def back_to_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = query.from_user
    if await is_user_in_channel(user.id, context):
        main_keyboard = [
            [InlineKeyboardButton("➕ Add Me to Your Channel", url=f"https://t.me/{context.bot.username}?startchannel=start")],
            [InlineKeyboardButton("❓ Help", callback_data='help')],
            [InlineKeyboardButton("👑 Buy Premium", callback_data='buy_premium')]
        ]
        await query.edit_message_text(
            f"Hi {user.first_name}! Welcome back.",
            reply_markup=InlineKeyboardMarkup(main_keyboard)
        )
    else:
        join_keyboard = [
            [InlineKeyboardButton("Join Our Channel", url="https://t.me/Asbhai_bsr")],
            [InlineKeyboardButton("✅ Verify", callback_data='verify_join')],
        ]
        await query.edit_message_text(
            "Please join our channel to use this bot.",
            reply_markup=InlineKeyboardMarkup(join_keyboard)
        )

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    help_text = (
        "**❓ Help & Support**\n\n"
        "**Forwarding:**\n"
        "Simply add me to your channel as an administrator. Then, use the `/addchannel` command in our private chat with me, providing the channel ID or by replying to a message from the channel. I will automatically remove the 'Forwarded from' tag from all forwarded messages. This is a free feature, but can only be used on **one channel per user**.\n\n"
        "**Premium:**\n"
        "To get premium features and add me to unlimited channels, click the '👑 Buy Premium' button and follow the instructions. For any further assistance, you can contact the admin.\n\n"
        "**How to add me to your channel:**\n"
        "1. Click the 'Add Me to Your Channel' button.\n"
        "2. Select the channel where you want to add the bot.\n"
        "3. Make sure to give the bot admin permissions to 'Delete messages' and 'Post messages' so it can remove forwarded tags correctly.\n"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 Back", callback_data='back_to_start')]
    ]
    await query.edit_message_text(text=help_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def buy_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    premium_text = (
        "👑 **Buy Premium Service**\n\n"
        "Our premium service allows you to add the bot to unlimited channels for **₹300 for 1 year**.\n\n"
        "**How to Pay:**\n"
        "1.  Pay the amount via UPI to this ID: `arsadsaifi8272@ibl`\n"
        "2.  Take a screenshot of the payment.\n"
        "3.  Click the button below to send the screenshot to our admin for verification."
    )
    keyboard = [
        [InlineKeyboardButton("💳 Send Screenshot", url=f"https://t.me/{ADMIN_USERNAME}")],
        [InlineKeyboardButton("🔙 Back", callback_data='back_to_start')]
    ]
    await query.edit_message_text(
        text=premium_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def add_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    try:
        args = context.args
        premium_user_id = int(args[0])
        premium_duration = 365 # 1 year
        
        expiry_date = datetime.datetime.now() + timedelta(days=premium_duration)
        
        user_doc = await users_collection.find_one({'user_id': premium_user_id})
        if not user_doc:
            await update.message.reply_text(f"User with ID `{premium_user_id}` not found in the database.")
            return

        await premium_users_collection.update_one(
            {'user_id': premium_user_id},
            {'$set': {'expiry_date': expiry_date, 'added_by_admin': ADMIN_ID}},
            upsert=True
        )
        
        await update.message.reply_text(f"User `{premium_user_id}` has been granted premium for 1 year.")
        
        try:
            await context.bot.send_message(chat_id=premium_user_id, text="🥳 Your premium subscription has been activated! You can now add the bot to unlimited channels.")
        except Exception:
            logging.warning(f"Failed to send a message to the user {premium_user_id} about premium activation.")

    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /add_premium <user_id>")

async def remove_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    try:
        args = context.args
        premium_user_id = int(args[0])
        result = await premium_users_collection.delete_one({'user_id': premium_user_id})
        if result.deleted_count > 0:
            await update.message.reply_text(f"Premium status for user `{premium_user_id}` has been removed successfully.")
        else:
            await update.message.reply_text(f"User `{premium_user_id}` does not have an active premium subscription.")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /remove_premium <user_id>")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    user_count = await users_collection.count_documents({})
    channel_count = await channels_collection.count_documents({})
    premium_count = await premium_users_collection.count_documents({'expiry_date': {'$gt': datetime.datetime.now()}})
    stats_text = (
        f"📊 **Bot Stats**\n\n"
        f"Total Users: {user_count}\n"
        f"Total Channels Bot is in: {channel_count}\n"
        f"Premium Users: {premium_count}"
    )
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def premium_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    premium_users = premium_users_collection.find({})
    premium_list = await premium_users.to_list(length=None)
    if not premium_list:
        await update.message.reply_text("No premium users found.")
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
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Please reply to a message you want to broadcast to users.")
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
    await update.message.reply_text(f"User Broadcast complete. Sent to {sent_count} users. Blocked by {blocked_count} users.")

async def channel_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Please reply to a message you want to broadcast to channels.")
        return
    message_to_broadcast = update.message.reply_to_message
    channels = channels_collection.find({})
    sent_count = 0
    failed_count = 0
    for channel_doc in await channels.to_list(length=None):
        channel_id = channel_doc['channel_id']
        premium_channel = await premium_users_collection.find_one({'user_id': channel_doc.get('added_by_user')})
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
    await update.message.reply_text(f"Channel Broadcast complete. Sent to {sent_count} non-premium channels. Failed on {failed_count} channels.")


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    # --- Handlers ---
    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('addchannel', addchannel_command))
    application.add_handler(CommandHandler('remove_tags', remove_tags_command))
    application.add_handler(CallbackQueryHandler(verify_callback, pattern='^verify_join$'))
    application.add_handler(CallbackQueryHandler(buy_premium_callback, pattern='^buy_premium$'))
    application.add_handler(CallbackQueryHandler(help_callback, pattern='^help$'))
    application.add_handler(CallbackQueryHandler(back_to_start_callback, pattern='^back_to_start$'))
    application.add_handler(CommandHandler('stats', stats_command))
    application.add_handler(CommandHandler('premium_stats', premium_stats_command))
    application.add_handler(CommandHandler('broadcast', broadcast_command))
    application.add_handler(CommandHandler('channel_broadcast', channel_broadcast_command))
    application.add_handler(CommandHandler('add_premium', add_premium_command))
    application.add_handler(CommandHandler('remove_premium', remove_premium_command))
    
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.DOCUMENT), handle_all_messages_in_channel))
    application.add_handler(ChatMemberHandler(track_chat_member, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER))

    # --- Start the bot ---
    if WEBHOOK_URL:
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
            allowed_updates=["message", "chat_member", "callback_query"]
        )
    else:
        application.run_polling()

if __name__ == '__main__':
    main()
