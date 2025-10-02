# main.py
import logging
from telegram import Update
from typing import Optional
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TimedOut, NetworkError
from telegram.constants import ParseMode

import pystray
from PIL import Image
import threading
import time

from config import TELEGRAM_TOKEN, OPENROUTER_API_KEY, AI_MODEL, CRISIS_KEYWORDS, MAX_HISTORY_LENGTH, CRISIS_RESOURCES
from prompts import WELCOME_MESSAGE, HELP_MESSAGE, RESET_MESSAGE, API_ERROR_MESSAGE, CRISIS_STEP_1_MESSAGE, CRISIS_SYSTEM_PROMPT
from ai_handler import get_ai_response
from database import init_db, get_user, create_or_update_user, increment_daily_chat, add_warning, update_mental_scores, save_message, get_user_history, append_chat_log, update_chat_end_time, get_inactive_users, get_worst_users, reset_all_daily_chats
from prompts import VIOLATION_CHECK_PROMPT, MENTAL_ASSESSMENT_PROMPT
from config import VIOLATION_KEYWORDS
from datetime import datetime, timedelta
import schedule
import time as time_module
import sqlite3
import asyncio


# 配置日志
import sys

# 配置控制台编码为UTF-8以支持表情符号
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# 抑制 httpx 的 INFO 日志，只显示 WARNING 及以上级别
logging.getLogger('httpx').setLevel(logging.WARNING)

# 初始化数据库
init_db()

# 系统托盘相关
bot_running = False
application = None

async def safe_send_message(bot, chat_id: int, text: str, parse_mode=None):
    """安全发送消息，捕获网络错误"""
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    except (TimedOut, NetworkError) as e:
        logger.warning(f"发送消息失败到 {chat_id}: {e}")
        # 尝试不带 parse_mode 重发
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as e2:
            logger.error(f"备用发送也失败: {e2}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 Telegram API 错误，特别是网络超时"""
    logger.error(f"处理更新时发生错误 {context.error}")
    if isinstance(context.error, (TimedOut, NetworkError)):
        logger.warning(f"网络超时或错误: {context.error}. 忽略并继续运行。")
    # 可以添加重试逻辑或其他处理，但这里仅记录

def create_image():
    """创建一个更明显的图标，带文本"""
    from PIL import ImageDraw, ImageFont
    try:
        image = Image.new('RGB', (64, 64), color='lightblue')
        draw = ImageDraw.Draw(image)
        # 尝试加载字体，fallback到默认
        try:
            font = ImageFont.truetype("arial.ttf", 12)
        except:
            font = ImageFont.load_default()
        draw.text((10, 25), "MFA", fill='darkblue', font=font)
        return image
    except Exception as e:
        logger.warning(f"图标创建失败，使用默认: {e}")
        image = Image.new('RGB', (64, 64), color='blue')
        return image

def on_quit(icon, item):
    icon.stop()

def start_bot(icon, item):
    global bot_running, application
    if not bot_running:
        logger.info("通过托盘手动启动 Bot")
        _init_and_start_bot()
        icon.notify('Bot 已启动', 'Mind First Aid Kit')
    else:
        icon.notify('Bot 已在运行', 'Mind First Aid Kit')

def stop_bot(icon, item):
    global bot_running, application
    if bot_running and application:
        bot_running = False
        application.stop_running()
        logger.info("Bot 已停止")
        icon.notify('Bot 已停止', 'Mind First Aid Kit')
    else:
        icon.notify('Bot 未运行', 'Mind First Aid Kit')

def check_inactive_users():
    """每分钟检查不活跃用户，10min无消息标记结束"""
    now = datetime.now()
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('''
        SELECT user_id FROM users
        WHERE last_message_time IS NOT NULL
        AND last_chat_end_time IS NULL
        AND datetime(last_message_time) < datetime('now', '-10 minutes')
    ''')
    inactive = [row[0] for row in c.fetchall()]
    for user_id in inactive:
        update_chat_end_time(user_id)
    conn.close()

async def send_followup_greetings():
    """每小时发送跟进问候给3小时前结束聊天的用户"""
    global application
    if not application:
        return
    inactive_users = get_inactive_users(3)
    for user_id in inactive_users:
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text="好点了吗？如果需要，我在这里听着。"
            )
        except Exception:
            pass  # 发送失败忽略

async def send_worst_users_greetings():
    """每天发送问候给心理状态最差的3人"""
    global application
    if not application:
        return
    worst = get_worst_users(3)
    for w in worst:
        try:
            await application.bot.send_message(
                chat_id=w['user_id'],
                text="最近怎么样？如果感觉不太好，记得寻求支持哦。"
            )
        except Exception:
            pass  # 发送失败忽略

def daily_reset():
    """每天重置聊天次数"""
    reset_all_daily_chats()

def run_scheduler():
    """运行调度器"""
    def run_async_task(coro):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(coro)
        loop.close()

    schedule.every(1).minutes.do(check_inactive_users)
    schedule.every(1).hours.do(lambda: run_async_task(send_followup_greetings()))
    schedule.every(1).hours.do(lambda: run_async_task(send_worst_users_greetings()))
    schedule.every().day.at("00:00").do(daily_reset)
    
    while True:
        schedule.run_pending()
        time_module.sleep(1)

def _init_and_start_bot():
    """初始化并启动 Bot"""
    global bot_running, application
    if not TELEGRAM_TOKEN:
        logger.error("错误：未设置 TELEGRAM_BOT_TOKEN 环境变量。")
        return
    from telegram.request import HTTPXRequest

    if application is None:
        request = HTTPXRequest(
            connect_timeout=60.0,
            read_timeout=60.0,
            pool_timeout=60.0,
            write_timeout=60.0
        )
        application = Application.builder().token(TELEGRAM_TOKEN).request(request).build()
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("reset", reset_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_error_handler(error_handler)
        logger.info("错误处理器已注册")
    
    def run_bot():
        global bot_running
        bot_running = True
        logger.info("机器人自动启动成功！托盘图标应在任务栏右下角可见。")
        logger.info("使用 /help 测试命令，或发送消息测试响应。")
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            application.run_polling()  # type: ignore
        finally:
            loop.close()
    
    if not bot_running:
        threading.Thread(target=run_bot, daemon=True).start()

def setup_tray():
    menu = pystray.Menu(
        pystray.MenuItem("启动 Bot", start_bot),
        pystray.MenuItem("停止 Bot", stop_bot),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit)
    )
    image = create_image()
    icon = pystray.Icon("Mind First Aid Kit", image, menu=menu)
    
    # 启动调度器线程
    threading.Thread(target=run_scheduler, daemon=True).start()
    
    # 自动启动 Bot
    _init_and_start_bot()
    
    logger.info("系统托盘已设置，图标: 蓝色 MFA (任务栏右下角)")
    icon.run()

# --- 辅助函数 ---
def is_crisis_message(text: Optional[str]) -> bool:
    """检测用户输入是否包含危机关键词"""
    if text is None:
        return False
    return any(keyword in text for keyword in CRISIS_KEYWORDS)

# --- 命令处理函数 ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /start 命令"""
    if update.effective_chat is None or update.message is None:
        return
    chat_id = update.effective_chat.id
    create_or_update_user(chat_id, is_in_crisis=False)
    try:
        await safe_send_message(context.bot, chat_id, WELCOME_MESSAGE, ParseMode.HTML)
    except Exception as e:
        logger.error(f"/start 命令发送失败: {e}")
        # 尝试简单文本发送
        try:
            await context.bot.send_message(chat_id=chat_id, text=WELCOME_MESSAGE.replace('<b>', '').replace('</b>', '').replace('<i>', '').replace('</i>', ''))
        except Exception as e2:
            logger.error(f"备用 /start 发送也失败: {e2}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /help 命令"""
    if update.message is None or update.effective_chat is None:
        return
    chat_id = update.effective_chat.id
    await safe_send_message(context.bot, chat_id, HELP_MESSAGE, ParseMode.HTML)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /reset 命令，重置会话"""
    if update.effective_chat is None or update.message is None:
        return
    chat_id = update.effective_chat.id
    create_or_update_user(chat_id, is_in_crisis=False)
    await safe_send_message(context.bot, chat_id, RESET_MESSAGE)

# --- 消息处理核心逻辑 ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理用户的所有文本消息"""
    if update.effective_chat is None or update.message is None or update.message.text is None:
        logger.warning("无效消息更新")
        return
    chat_id = update.effective_chat.id
    user_text: str = update.message.text
    logger.info(f"收到用户 {chat_id} 消息: {user_text[:50]}...")

    user = get_user(chat_id)
    if user is None:
        create_or_update_user(chat_id, is_in_crisis=False)
        user = get_user(chat_id)

    if user and user['is_banned']:
        await safe_send_message(context.bot, chat_id, "❌ 您已被拉黑，无法使用此机器人。")
        logger.warning(f"用户 {chat_id} 被禁")
        return

    # 检查违规内容
    try:
        violation_check = await get_ai_response([{"role": "system", "content": VIOLATION_CHECK_PROMPT.format(message=user_text)}])
        if violation_check == "VIOLATION" or any(keyword in user_text.lower() for keyword in VIOLATION_KEYWORDS):
            warning_count = add_warning(chat_id)
            if update.message:
                if warning_count < 3:
                    await safe_send_message(context.bot, chat_id, f"⚠️ 警告 {warning_count}/3: 请避免发送违规内容（暴力、邪教、色情）。继续将导致拉黑。")
                else:
                    await safe_send_message(context.bot, chat_id, "🚫 您已被拉黑3次警告，无法继续使用。")
            logger.warning(f"用户 {chat_id} 违规警告: {warning_count}")
            return
    except Exception as e:
        logger.error(f"违规检查失败: {e}")

    # 检查聊天次数限制
    if not increment_daily_chat(chat_id):
        await safe_send_message(context.bot, chat_id, "📅 今日聊天次数已达上限（100次），请明天再聊。")
        logger.info(f"用户 {chat_id} 达到聊天上限")
        return

    # 更新最后消息时间
    create_or_update_user(chat_id, last_message_time=datetime.now().isoformat())

    # 加载历史
    history = get_user_history(chat_id, MAX_HISTORY_LENGTH * 2)
    is_in_crisis = user['is_in_crisis'] if user else False

    # 保存用户消息
    save_message(chat_id, "user", user_text)
    append_chat_log(chat_id, "user", user_text)

    # **心理危机处理协议**
    if not is_in_crisis and is_crisis_message(user_text):
        logger.warning(f"🚨 用户 {chat_id} 触发危机协议关键词。")
        create_or_update_user(chat_id, is_in_crisis=True)
        
        # Step 1: 立即验证与稳定
        await safe_send_message(context.bot, chat_id, CRISIS_STEP_1_MESSAGE, ParseMode.HTML)
        
        # Step 2: 强制资源引导
        await safe_send_message(context.bot, chat_id, CRISIS_RESOURCES, ParseMode.HTML)
        return # 终止本次交互，等待用户对安全问题的回应

    # 如果用户已处于危机模式
    if is_in_crisis:
        logger.info(f"用户 {chat_id} 处于危机模式，发送引导性回复。")
        # Step 3: 限制AI响应
        history.append({"role": "user", "content": user_text})
        try:
            ai_response = await asyncio.wait_for(get_ai_response(history, system_prompt=CRISIS_SYSTEM_PROMPT, max_tokens=100), timeout=30.0)
        except asyncio.TimeoutError:
            ai_response = None
            logger.error(f"危机模式 AI 响应超时 (用户 {chat_id})")
        except Exception as e:
            ai_response = None
            logger.error(f"危机模式 AI 错误: {e}")
        
        if ai_response:
            save_message(chat_id, "assistant", ai_response)
            append_chat_log(chat_id, "assistant", ai_response)
            await safe_send_message(context.bot, chat_id, ai_response)
        else:
            # 如果AI出错，也要发送紧急资源
            await safe_send_message(context.bot, chat_id, API_ERROR_MESSAGE + "\n\n" + CRISIS_RESOURCES, ParseMode.HTML)
        return

    # --- 正常聊天模式 ---
    history.append({"role": "user", "content": user_text})
    
    # 保持会话历史在一定长度内
    if len(history) > MAX_HISTORY_LENGTH * 2:
        history = history[-MAX_HISTORY_LENGTH * 2:]

    # 获取 AI 回复
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    except Exception as e:
        logger.warning(f"发送 typing 动作失败 (用户 {chat_id}): {e}")
    logger.info(f"生成 AI 响应中... (用户 {chat_id})")
    try:
        ai_response = await asyncio.wait_for(get_ai_response(history), timeout=30.0)
    except asyncio.TimeoutError:
        ai_response = None
        logger.error(f"AI 响应超时 (用户 {chat_id})")
    except Exception as e:
        ai_response = None
        logger.error(f"AI 生成错误: {e} (用户 {chat_id})")

    if ai_response:
        logger.info(f"AI 响应生成成功 (用户 {chat_id}): {ai_response[:50]}...")
        save_message(chat_id, "assistant", ai_response)
        append_chat_log(chat_id, "assistant", ai_response)
        if update.message:
            await safe_send_message(context.bot, chat_id, ai_response)
        
        # 心理状态评估
        try:
            assessment_history = history + [{"role": "assistant", "content": ai_response}]
            assessment_prompt = MENTAL_ASSESSMENT_PROMPT.format(history=str(assessment_history))
            assessment_response = await asyncio.wait_for(get_ai_response([{"role": "system", "content": assessment_prompt}]), timeout=20.0)
            import json
            if assessment_response is not None:
                assessment = json.loads(assessment_response)
                update_mental_scores(chat_id, assessment.get('depression', 0), assessment.get('anxiety', 0))
                logger.info(f"心理评估更新 (用户 {chat_id}): 抑郁={assessment.get('depression', 0)}, 焦虑={assessment.get('anxiety', 0)}")
        except Exception as e:
            logger.warning(f"心理评估失败: {e}")
    else:
        error_msg = API_ERROR_MESSAGE + "\n\n💡 可能原因：网络问题或 API 限额。请稍后重试，或检查配置。"
        await safe_send_message(context.bot, chat_id, error_msg, ParseMode.HTML)
        logger.error(f"AI 响应失败，发送错误消息 (用户 {chat_id})")


def main() -> None:
    """启动机器人与系统托盘"""
    # 增强日志配置，添加文件输出
    # 移除文件处理器，因为已在basicConfig中配置
    for handler in logger.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            logger.removeHandler(handler)
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler('bot.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.info("=== Mind First Aid Kit Bot 启动 ===")
    logger.info(f"TELEGRAM_TOKEN: {'设置' if TELEGRAM_TOKEN else '未设置'}")
    logger.info(f"OPENROUTER_API_KEY: {'设置' if OPENROUTER_API_KEY else '未设置'}")
    logger.info(f"AI_MODEL: {AI_MODEL}")
    
    setup_tray()

if __name__ == '__main__':
    main()
