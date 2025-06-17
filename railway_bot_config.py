#!/usr/bin/env python3
"""
Telegram Bot Configuration for Railway/Render Deployment
Fixed for Python 3.13 compatibility and multiple instance conflicts
"""

import logging
import os
import signal
import sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ChatMemberHandler
import asyncio
import telegram

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration - can be overridden by environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN', "7837182225:AAGWPDP5Bco3h-0WLrwrV8UXuI8eVgjF3qg")
# Source group chat ID (where media comes from)
SOURCE_GROUP_ID = int(os.getenv('SOURCE_GROUP_ID', '-4926087910'))
# Archive group chat ID (where media gets forwarded to)
ARCHIVE_GROUP_ID = int(os.getenv('ARCHIVE_GROUP_ID', '-2657848581'))

# Validate configuration
if not BOT_TOKEN:
    logger.error("BOT_TOKEN is required")
    sys.exit(1)
if SOURCE_GROUP_ID == 0:
    logger.error("SOURCE_GROUP_ID is required")
    sys.exit(1)
if ARCHIVE_GROUP_ID == 0:
    logger.error("ARCHIVE_GROUP_ID is required")
    sys.exit(1)

# Get port from environment (Railway provides this)
PORT = int(os.getenv('PORT', '8080'))

# Global application instance for cleanup
application = None

# Store pending verifications {user_id: {chat_id: chat_id, message_id: message_id}}
pending_verifications = {}

# Quiz question and answers
QUIZ_QUESTION = "I am joining this group because…"
QUIZ_OPTIONS = [
    "I am a Knotty Beast",
    "I have a passion for the well-being of Animals",
    "I want to be a gay Zoologist when I Grow up",
    "Gay Zookeepers seeking other Gay Zookeeper personal ads"
]
CORRECT_ANSWER = 0  # Index of correct answer (first option)

WELCOME_MESSAGE = """Welcome to the Zoo you dirty perv!

I'm the group creator/admin @Slammy1 — thanks for joining!

Ground Rules

• Posting Videos/Photos:

You are encouraged to share content relating to the group. 
Gay Zoo = No human females.
 
I will not be policing the content of what is posted and will assume that if you're posting any content that is not of yourself, you either 

      — Have the owners consent 
      — The owner previously posted the
content publicly, and therefore consent is given.

That being said, if you see something that you feel should be taken down, please reach out to me directly and it will be removed. 

• Sharing the Group:
If you want to invite someone else, I ask that you be smart about it. Don't post the invite link in any groups for everyone to just click and join; that's how things get reported. 

So please, don't let the cat out of the bag! Instead, hop in the bag and fuck that naughty pussy! 

• Be Respectful:
The last thing is to be kind to one another; we're all knotty, perverted homos and together we're
like a deranged, disfigured family! 
Now, get your brother to stop sucking the dog's dick and get that knot in your ass like I taught you! 

—Slammy"""

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}. Shutting down gracefully...")
    if application:
        logger.info("Stopping application...")
        try:
            asyncio.create_task(application.stop())
        except:
            pass
    sys.exit(0)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! I'm your media archive bot. Send me any media and I'll forward it to the archive group!"
    )

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle new members joining the group."""
    # Only handle joins in the source group
    if update.effective_chat.id != SOURCE_GROUP_ID:
        return
    
    for member in update.message.new_chat_members:
        # Skip if it's the bot itself
        if member.id == context.bot.id:
            continue
            
        user_id = member.id
        chat_id = update.effective_chat.id
        
        try:
            # Restrict the user until they verify
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=telegram.ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                    can_change_info=False,
                    can_invite_users=False,
                    can_pin_messages=False
                )
            )
            
            # Create quiz keyboard
            keyboard = []
            for i, option in enumerate(QUIZ_OPTIONS):
                keyboard.append([InlineKeyboardButton(
                    f"{i+1}. {option}", 
                    callback_data=f"quiz_{user_id}_{i}"
                )])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Send verification message
            verification_msg = await update.message.reply_text(
                f"👋 Welcome @{member.username or member.first_name}!\n\n"
                f"To join this group, please answer this question:\n\n"
                f"**{QUIZ_QUESTION}**\n\n"
                f"⏰ You have 5 minutes to answer, or you'll be removed from the group.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
            # Store verification info
            pending_verifications[user_id] = {
                'chat_id': chat_id,
                'message_id': verification_msg.message_id,
                'username': member.username or member.first_name
            }
            
            # Set timer to kick user if they don't verify within 5 minutes
            context.job_queue.run_once(
                kick_unverified_user,
                300,  # 5 minutes
                data={'user_id': user_id, 'chat_id': chat_id},
                name=f"kick_{user_id}"
            )
            
        except Exception as e:
            logger.error(f"Error handling new member {user_id}: {e}")

async def handle_quiz_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quiz answer button clicks."""
    query = update.callback_query
    await query.answer()
    
    # Parse callback data
    try:
        _, user_id_str, answer_str = query.data.split('_')
        user_id = int(user_id_str)
        answer_index = int(answer_str)
    except (ValueError, IndexError):
        return
    
    # Check if this user is allowed to answer (the person who needs to verify)
    if query.from_user.id != user_id:
        await query.answer("❌ This verification is not for you!", show_alert=True)
        return
    
    # Check if user is still pending verification
    if user_id not in pending_verifications:
        await query.answer("❌ Verification expired or already completed!", show_alert=True)
        return
    
    verification_info = pending_verifications[user_id]
    chat_id = verification_info['chat_id']
    username = verification_info['username']
    
    # Cancel the kick timer
    current_jobs = context.job_queue.get_jobs_by_name(f"kick_{user_id}")
    for job in current_jobs:
        job.schedule_removal()
    
    if answer_index == CORRECT_ANSWER:
        # Correct answer - grant full permissions
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=telegram.ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_change_info=False,
                    can_invite_users=False,
                    can_pin_messages=False
                )
            )
            
            # Update the verification message
            await query.edit_message_text(
                f"✅ Welcome @{username}! You've successfully joined the group.\n\n"
                f"🎉 Check your private messages for important information!"
            )
            
            # Send welcome message in private chat
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=WELCOME_MESSAGE
                )
            except Exception as e:
                logger.warning(f"Could not send private welcome message to {user_id}: {e}")
                # If private message fails, send in group
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"@{username}, welcome! Please check your private messages. If you didn't receive a message, please start a chat with me first by clicking @{context.bot.username}",
                    reply_to_message_id=query.message.message_id
                )
            
            logger.info(f"User {username} ({user_id}) successfully verified and joined group {chat_id}")
            
        except Exception as e:
            logger.error(f"Error granting permissions to user {user_id}: {e}")
    else:
        # Wrong answer - kick the user
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)  # Unban so they can rejoin later
            
            await query.edit_message_text(
                f"❌ @{username} gave an incorrect answer and has been removed from the group.\n\n"
                f"The correct answer was: **{QUIZ_OPTIONS[CORRECT_ANSWER]}**",
                parse_mode='Markdown'
            )
            
            logger.info(f"User {username} ({user_id}) gave wrong answer and was kicked from group {chat_id}")
            
        except Exception as e:
            logger.error(f"Error kicking user {user_id}: {e}")
    
    # Clean up
    if user_id in pending_verifications:
        del pending_verifications[user_id]

async def kick_unverified_user(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kick users who didn't verify within the time limit."""
    job_data = context.job.data
    user_id = job_data['user_id']
    chat_id = job_data['chat_id']
    
    if user_id not in pending_verifications:
        return  # Already verified or handled
    
    verification_info = pending_verifications[user_id]
    username = verification_info['username']
    message_id = verification_info['message_id']
    
    try:
        # Kick the user
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)  # Unban so they can rejoin later
        
        # Update the verification message
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"⏰ @{username} didn't answer the verification question in time and has been removed from the group."
        )
        
        logger.info(f"User {username} ({user_id}) timed out and was kicked from group {chat_id}")
        
    except Exception as e:
        logger.error(f"Error kicking unverified user {user_id}: {e}")
    
    # Clean up
    if user_id in pending_verifications:
        del pending_verifications[user_id]

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    help_text = """Available commands:
/start - Start the bot
/help - Show this help message
/echo <message> - Echo your message
/info - Get chat information
/getid - Get current chat ID (useful for finding archive group ID)

Send any media (photos, videos, documents, audio, stickers, etc.) and I'll forward them to the archive group!"""
    await update.message.reply_text(help_text)

async def echo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user's message from the /echo command."""
    if context.args:
        message = ' '.join(context.args)
        await update.message.reply_text(f"You said: {message}")
    else:
        await update.message.reply_text("Please provide a message to echo. Usage: /echo <message>")

async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get the current chat ID - useful for finding group IDs."""
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    chat_title = update.effective_chat.title if update.effective_chat.title else "N/A"
    
    await update.message.reply_text(
        f"Chat ID: `{chat_id}`\n"
        f"Chat Type: {chat_type}\n"
        f"Chat Title: {chat_title}\n\n"
        f"Copy this chat ID to use as ARCHIVE_GROUP_ID in the bot code.",
        parse_mode='Markdown'
    )

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send information about the chat."""
    chat = update.effective_chat
    user = update.effective_user
    
    info_text = f"""Chat Information:
- Chat ID: {chat.id}
- Chat Type: {chat.type}
- User ID: {user.id}
- Username: @{user.username if user.username else 'N/A'}
- First Name: {user.first_name}
- Last Name: {user.last_name if user.last_name else 'N/A'}"""
    await update.message.reply_text(info_text)

async def echo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo any text message."""
    await update.message.reply_text(f"You said: {update.message.text}")

async def forward_media_to_archive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward media messages to archive group."""
    # Only forward media from the specific source group
    if update.effective_chat.id != SOURCE_GROUP_ID:
        return
    
    try:
        # Forward the message to the archive group
        await context.bot.forward_message(
            chat_id=ARCHIVE_GROUP_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )
        
        # Log the forwarded media (optional - comment out if you don't want confirmation messages)
        user = update.effective_user
        media_type = "media"
        if update.message.photo:
            media_type = "photo"
        elif update.message.video:
            media_type = "video"
        elif update.message.document:
            media_type = "document"
        elif update.message.audio:
            media_type = "audio"
        elif update.message.voice:
            media_type = "voice message"
        elif update.message.video_note:
            media_type = "video note"
        elif update.message.sticker:
            media_type = "sticker"
        
        # Optional: Send confirmation (you can comment this out if it's too spammy)
        # await update.message.reply_text(f"📁 {media_type.title()} forwarded to archive!")
        
        logger.info(f"Forwarded {media_type} from {user.first_name} to archive")
        
    except Exception as e:
        logger.error(f"Failed to forward media to archive: {e}")
        # Optional: Send error message (you can comment this out in production)
        # await update.message.reply_text("❌ Failed to forward to archive.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo messages."""
    await forward_media_to_archive(update, context)

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle video messages."""
    await forward_media_to_archive(update, context)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document messages."""
    await forward_media_to_archive(update, context)

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle audio messages."""
    await forward_media_to_archive(update, context)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages."""
    await forward_media_to_archive(update, context)

async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle video note messages."""
    await forward_media_to_archive(update, context)

async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle sticker messages."""
    await forward_media_to_archive(update, context)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)

def main() -> None:
    """Start the bot."""
    global application
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("Starting bot with:")
    logger.info(f"Source Group ID: {SOURCE_GROUP_ID}")
    logger.info(f"Archive Group ID: {ARCHIVE_GROUP_ID}")
    logger.info(f"Port: {PORT}")
    
    try:
        # Create the Application
        application = Application.builder().token(BOT_TOKEN).build()

        # Add handlers for new members and quiz answers
        application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))
        application.add_handler(CallbackQueryHandler(handle_quiz_answer, pattern=r"^quiz_\d+_\d+$"))
        
        # Add command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("echo", echo_command))
        application.add_handler(CommandHandler("info", info_command))
        application.add_handler(CommandHandler("getid", get_chat_id))

        # Add message handlers for all media types
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        application.add_handler(MessageHandler(filters.VIDEO, handle_video))
        application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
        application.add_handler(MessageHandler(filters.VOICE, handle_voice))
        application.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note))
        application.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
        
        # Text messages (non-command)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_message))

        # Add error handler
        application.add_error_handler(error_handler)

        # Run the bot with improved error handling
        logger.info("Bot is starting...")
        application.run_polling(
            allowed_updates=["message", "chat_member", "callback_query"],
            drop_pending_updates=True  # This helps avoid conflicts with multiple instances
        )
        
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
