import os
import random
import logging
import asyncio
from contextlib import asynccontextmanager
import aiosqlite

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters
)

# ==========================================
# LOGGING & KONFIGURASI BOT
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("menfess_cc_bot")

TOKEN = os.getenv("TELEGRAM_PHOTOBOOTH_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", BASE_DIR)
DB_NAME = os.path.join(DB_DIR, "menfess_cc.db")

STICKER_FILENAME = "sticker_punkshit.webm"

# List Title Punk Random
PUNK_TITLES = [
    "Gitar Rusak", "Jaket Kulit Rombeng", "Sepatu Boots Bolong",
    "Stiker Distorsi", "Amplifier Pecah", "Kaset Pita Vintage",
    "Tali Sepatu Merah", "Pin Peniti Skena", "Kacamata Hitam Malam",
    "Buku Catatan Lirik", "Kaos Band Becek", "Rantai Dompet Geser"
]

# ==========================================
# DATABASE HELPER
# ==========================================
@asynccontextmanager
async def get_db_connection():
    db = await aiosqlite.connect(DB_NAME, timeout=30.0)
    try:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        yield db
    finally:
        await db.close()

async def init_db():
    os.makedirs(os.path.dirname(DB_NAME), exist_ok=True)
    async with get_db_connection() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS menfess_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_msg_id INTEGER,
                sender_id INTEGER,
                sender_username TEXT,
                target_username TEXT,
                punk_title TEXT,
                pesan TEXT,
                guesses_left INTEGER DEFAULT 2,
                status TEXT DEFAULT 'active'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS menfess_reactions (
                msg_id INTEGER,
                user_id INTEGER,
                emoji TEXT,
                PRIMARY KEY (msg_id, user_id)
            )
        """)
        await db.commit()

async def post_init(application):
    await init_db()

# ==========================================
# KEYBOARD & REACTION BUILDERS
# ==========================================
async def build_channel_keyboard(msg_id: int, target_username: str, guesses_left: int = 2, status: str = 'active'):
    counts = {'🔥': 0, '💔': 0, '🤡': 0}
    async with get_db_connection() as db:
        async with db.execute("SELECT emoji, COUNT(*) FROM menfess_reactions WHERE msg_id = ? GROUP BY emoji", (msg_id,)) as cursor:
            rows = await cursor.fetchall()
            for emoji, cnt in rows:
                if emoji in counts:
                    counts[emoji] = cnt

    btn_row1 = [
        InlineKeyboardButton(f"🔥 {counts['🔥']}", callback_data=f"react_{msg_id}_🔥"),
        InlineKeyboardButton(f"💔 {counts['💔']}", callback_data=f"react_{msg_id}_💔"),
        InlineKeyboardButton(f"🤡 {counts['🤡']}", callback_data=f"react_{msg_id}_🤡"),
    ]

    if status == 'solved':
        btn_row2 = [InlineKeyboardButton(f"✅ Terbongkar oleh @{target_username}", callback_data="none")]
    elif status == 'failed':
        btn_row2 = [InlineKeyboardButton("💀 Tetap Misterius (NT)", callback_data="none")]
    else:
        btn_row2 = [InlineKeyboardButton(f"🕵️ Tebak Sender ({guesses_left}x)", callback_data=f"guess_{msg_id}")]

    return InlineKeyboardMarkup([btn_row1, btn_row2])

# ==========================================
# HANDLERS WORKFLOW MENFESS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = (
        "<b>MENFESS CC BOT</b>\n\n"
        "Kirim pesan anonim ke channel CC PUNKSHIT.\n\n"
        "Silakan ketik <b>pesan menfess</b> kamu sekarang:"
    )
    await update.message.reply_text(text, parse_mode="HTML")
    context.user_data['state'] = "WAITING_TEXT"

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')

    if state == "WAITING_TEXT":
        context.user_data['pesan'] = update.message.text.strip()
        context.user_data['state'] = "WAITING_MEDIA"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➡️ Skip (Tanpa Media)", callback_data="skip_media")]
        ])
        await update.message.reply_text(
            "Pesan dicatat!\n\nSekarang kirim <b>Foto</b> atau <b>Musik/Audio</b> (opsional), atau tekan tombol Skip di bawah:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif state == "WAITING_TARGET":
        target = update.message.text.strip().replace("@", "")
        context.user_data['target_username'] = target
        await process_menfess_submission(update, context)

    elif state == "WAITING_GUESS_INPUT":
        guessed_name = update.message.text.strip().replace("@", "")
        post_id = context.user_data.get('guess_post_id')
        await process_guess_attempt(update, context, post_id, guessed_name)

async def handle_media_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if state == "WAITING_MEDIA":
        if update.message.photo:
            context.user_data['media_type'] = 'photo'
            context.user_data['media_file_id'] = update.message.photo[-1].file_id
        elif update.message.audio or update.message.voice:
            context.user_data['media_type'] = 'audio'
            context.user_data['media_file_id'] = (update.message.audio or update.message.voice).file_id

        context.user_data['state'] = "WAITING_TARGET"
        await update.message.reply_text(
            "Media diterima!\n\nKetik **username Telegram target** yang dituju (contoh: `@kenagain`):",
            parse_mode="HTML"
        )

# ==========================================
# BROADCAST TO CHANNEL
# ==========================================
async def process_menfess_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender = update.effective_user
    sender_uname = sender.username or sender.first_name
    pesan = context.user_data.get('pesan')
    target_uname = context.user_data.get('target_username')
    media_type = context.user_data.get('media_type')
    media_file_id = context.user_data.get('media_file_id')

    punk_title = f"{random.choice(PUNK_TITLES)} #{random.randint(100, 999)}"
    bot_username = f"@{context.bot.username}" if context.bot.username else ""

    caption = (
        f"<b>From:</b> {punk_title}\n"
        f"<b>To:</b> @{target_uname}\n\n"
        f"<i>\"{pesan}\"</i>\n\n"
        f"Yuk kirim di {bot_username}"
    )

    try:
        ch_msg = None
        if media_type == 'photo':
            ch_msg = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=media_file_id, caption=caption, parse_mode="HTML")
        elif media_type == 'audio':
            ch_msg = await context.bot.send_audio(chat_id=CHANNEL_ID, audio=media_file_id, caption=caption, parse_mode="HTML")
        else:
            ch_msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode="HTML")

        # Simpan Post ke Database
        async with get_db_connection() as db:
            cursor = await db.execute(
                "INSERT INTO menfess_posts (channel_msg_id, sender_id, sender_username, target_username, punk_title, pesan, guesses_left) VALUES (?, ?, ?, ?, ?, ?, 2)",
                (ch_msg.message_id, sender.id, sender_uname, target_uname, punk_title, pesan)
            )
            post_id = cursor.lastrowid
            await db.commit()

        # Update Keyboard Reaksi di Channel Post
        reply_markup = await build_channel_keyboard(post_id, target_uname, 2, 'active')
        await context.bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=ch_msg.message_id, reply_markup=reply_markup)

        # Send Stiker ke Channel
        sticker_path = os.path.join(BASE_DIR, STICKER_FILENAME)
        if os.path.exists(sticker_path):
            try:
                with open(sticker_path, 'rb') as st:
                    await context.bot.send_sticker(chat_id=CHANNEL_ID, sticker=st)
            except Exception as st_err:
                logger.error(f"Gagal send stiker: {st_err}")

        await update.message.reply_text("✨ Menfess kamu berhasil terbit di channel!", parse_mode="HTML")

    except Exception as e:
        logger.error(f"Gagal broadcast menfess: {e}")
        await update.message.reply_text("⚠️ Gagal mengirim menfess. Pastikan bot sudah jadi admin channel.")
    finally:
        context.user_data.clear()

# ==========================================
# CALLBACK ROUTER (REACTION & GUESS)
# ==========================================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user

    if data == "skip_media":
        context.user_data['state'] = "WAITING_TARGET"
        await query.edit_message_text(
            "Ketik **username Telegram target** yang dituju (contoh: `@kenagain`):",
            parse_mode="HTML"
        )
        return

    if data.startswith("react_"):
        _, post_id, emoji = data.split("_")
        post_id = int(post_id)

        async with get_db_connection() as db:
            async with db.execute("SELECT emoji FROM menfess_reactions WHERE msg_id = ? AND user_id = ?", (post_id, user.id)) as cursor:
                row = await cursor.fetchone()

            if row:
                if row[0] == emoji:
                    await db.execute("DELETE FROM menfess_reactions WHERE msg_id = ? AND user_id = ?", (post_id, user.id))
                else:
                    await db.execute("UPDATE menfess_reactions SET emoji = ? WHERE msg_id = ? AND user_id = ?", (emoji, post_id, user.id))
            else:
                await db.execute("INSERT INTO menfess_reactions (msg_id, user_id, emoji) VALUES (?, ?, ?)", (post_id, user.id, emoji))

            await db.commit()

            async with db.execute("SELECT channel_msg_id, target_username, guesses_left, status FROM menfess_posts WHERE id = ?", (post_id,)) as cursor:
                p_row = await cursor.fetchone()

        if p_row:
            ch_msg_id, target_uname, g_left, status = p_row
            markup = await build_channel_keyboard(post_id, target_uname, g_left, status)
            await context.bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=ch_msg_id, reply_markup=markup)

        await query.answer("Reaksi tercatat!")

    elif data.startswith("guess_"):
        post_id = int(data.split("_")[1])

        async with get_db_connection() as db:
            async with db.execute("SELECT target_username, guesses_left, status, punk_title FROM menfess_posts WHERE id = ?", (post_id,)) as cursor:
                row = await cursor.fetchone()

        if not row:
            await query.answer("Post tidak ditemukan.")
            return

        target_uname, g_left, status, punk_title = row
        clicker_uname = user.username or ""

        # Notif banner melayang jika bukan target yang klik
        if clicker_uname.lower() != target_uname.lower():
            await query.answer(f"Eits! Tombol ini khusus buat @{target_uname}!", show_alert=False)
            return

        if status != 'active' or g_left <= 0:
            await query.answer("Sesi tebak sender untuk menfess ini sudah selesai.", show_alert=False)
            return

        await query.answer()
        context.user_data['state'] = "WAITING_GUESS_INPUT"
        context.user_data['guess_post_id'] = post_id

        await context.bot.send_message(
            chat_id=user.id,
            text=f"🕵️ <b>TEBAK SENDER MENFESS</b>\n\nTitle: <b>{punk_title}</b>\nSisa Kesempatan: <b>{g_left}x</b>\n\nKetik username Telegram sender yang kamu curigai:",
            parse_mode="HTML"
        )

# ==========================================
# GAME LOGIC GUESSING
# ==========================================
async def process_guess_attempt(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int, guessed_name: str):
    async with get_db_connection() as db:
        async with db.execute("SELECT channel_msg_id, sender_username, target_username, guesses_left, punk_title FROM menfess_posts WHERE id = ?", (post_id,)) as cursor:
            row = await cursor.fetchone()

        if not row:
            await update.message.reply_text("Data menfess tidak ditemukan.")
            return

        ch_msg_id, sender_uname, target_uname, g_left, punk_title = row

        if guessed_name.lower() == sender_uname.lower():
            # TEBAKAN BENAR
            await db.execute("UPDATE menfess_posts SET status = 'solved' WHERE id = ?", (post_id,))
            await db.commit()

            markup = await build_channel_keyboard(post_id, target_uname, g_left, 'solved')
            await context.bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=ch_msg_id, reply_markup=markup)

            await update.message.reply_text(f"🎉 <b>TEBAKAN BENAR!</b>\n\nSender dari <b>{punk_title}</b> adalah @{sender_uname}!", parse_mode="HTML")

        else:
            # TEBAKAN SALAH
            g_left -= 1
            if g_left <= 0:
                await db.execute("UPDATE menfess_posts SET guesses_left = 0, status = 'failed' WHERE id = ?", (post_id,))
                await db.commit()

                markup = await build_channel_keyboard(post_id, target_uname, 0, 'failed')
                await context.bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=ch_msg_id, reply_markup=markup)

                await update.message.reply_text(f"💀 <b>TEBAKAN SALAH!</b>\n\nKesempatan kamu sudah habis. Identitas sender <b>{punk_title}</b> tetap misterius. Nice try ", parse_mode="HTML")
            else:
                await db.execute("UPDATE menfess_posts SET guesses_left = ? WHERE id = ?", (g_left, post_id))
                await db.commit()

                markup = await build_channel_keyboard(post_id, target_uname, g_left, 'active')
                await context.bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=ch_msg_id, reply_markup=markup)

                await update.message.reply_text(f"❌ <b>TEBAKAN SALAH!</b>\n\nSisa kesempatan kamu: <b>{g_left}x</b> lagi. Coba tebak username lain:", parse_mode="HTML")

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    asyncio.run(init_db())
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_router))

    app.add_handler(MessageHandler(filters.PHOTO | filters.AUDIO | filters.VOICE, handle_media_input))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    print("⚡ Bot Menfess CC Running...")
    app.run_polling()

if __name__ == "__main__":
    main()
