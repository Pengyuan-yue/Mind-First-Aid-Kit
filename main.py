# main.py
import logging
from telegram import Update
from typing import Optional
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TimedOut, NetworkError
from telegram.constants import ParseMode

import threading
import time

from config import TELEGRAM_TOKEN, OPENROUTER_API_KEY, AI_MODEL, CRISIS_KEYWORDS, MAX_HISTORY_LENGTH, CRISIS_RESOURCES
from prompts import WELCOME_MESSAGE, HELP_MESSAGE, RESET_MESSAGE, API_ERROR_MESSAGE, CRISIS_STEP_1_MESSAGE, CRISIS_SYSTEM_PROMPT, SYSTEM_PROMPT
from ai_handler import get_ai_response, get_ai_stream
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

# 全局变量
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
    except Exception as e:
        logger.error(f"发送消息异常: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 Telegram API 错误，特别是网络超时"""
    logger.error(f"处理更新时发生错误 {context.error}")
    if isinstance(context.error, (TimedOut, NetworkError)):
        logger.warning(f"网络超时或错误: {context.error}. 忽略并继续运行。")
    elif "RemoteProtocolError" in str(context.error) or "Event loop is closed" in str(context.error):
        logger.warning(f"协议或循环错误: {context.error}. 忽略并继续运行。")
    elif "Pool timeout" in str(context.error):
        logger.warning(f"连接池超时: {context.error}. 考虑增加池大小。")
    # 可以添加重试逻辑或其他处理，但这里仅记录

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
        try:
            loop.run_until_complete(coro)
        finally:
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
    global application
    if not TELEGRAM_TOKEN:
        logger.error("错误：未设置 TELEGRAM_BOT_TOKEN 环境变量。")
        return
    from telegram.request import HTTPXRequest

    if application is None:
        request = HTTPXRequest(
            connect_timeout=60.0,
            read_timeout=60.0,
            pool_timeout=120.0,
            write_timeout=60.0
        )
        application = Application.builder().token(TELEGRAM_TOKEN).request(request).build()
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("reset", reset_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_error_handler(error_handler)
        logger.info("错误处理器已注册")
    
    logger.info("机器人启动成功！")
    logger.info("使用 /help 测试命令，或发送消息测试响应。")
    try:
        application.run_polling()  # type: ignore
    except KeyboardInterrupt:
        logger.info("收到退出信号，正在停止机器人...")
        if application:
            application.stop_running()
        logger.info("机器人已停止运行")

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

    # 从接收消息开始发送 typing
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    except Exception as e:
        logger.warning(f"初始 typing 动作失败 (用户 {chat_id}): {e}")

    user = get_user(chat_id)
    if user is None:
        create_or_update_user(chat_id, is_in_crisis=False)
        user = get_user(chat_id)

    if user and user['is_banned']:
        await safe_send_message(context.bot, chat_id, "❌ 您已被拉黑，无法使用此机器人。")
        logger.warning(f"用户 {chat_id} 被禁")
        return

    # 无快速关键词检查，使用集成AI违规检测（单次调用）

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
        # Step 3: 限制AI响应（流式，集成违规检查）
        history.append({"role": "user", "content": user_text})
        full_response = ""
        message = None
        typing_task = None
        user = get_user(chat_id)
        warning_count = user.get('warning_count', 0) if user is not None else 0
        try:
            # 构建包含违规检查的系统提示
            violation_instruction = """
在生成响应前，内部检查用户最后一条消息是否包含违规内容（暴力、邪教、色情、政治敏感等）。如果是，立即输出以下警告消息并停止生成更多内容：
"⚠️ 警告：请避免发送违规内容（暴力、邪教、色情）。继续将导致拉黑。"
如果不是违规，正常生成响应，但保持危机模式：提供支持性、引导性回复，避免敏感话题。
"""
            system_prompt_with_violation = CRISIS_SYSTEM_PROMPT + violation_instruction

            # 发送初始消息
            initial_text = "正在思考中..."
            message = await context.bot.send_message(chat_id=chat_id, text=initial_text)
            last_sent = initial_text
            async for chunk in get_ai_stream(history, system_prompt=system_prompt_with_violation, max_tokens=100):
                if "错误" in chunk:
                    raise Exception(chunk)
                full_response += chunk
                if full_response != last_sent:
                    try:
                        await context.bot.edit_message_text(chat_id=chat_id, message_id=message.message_id, text=full_response)
                        last_sent = full_response
                    except Exception as edit_e:
                        if "Message is not modified" in str(edit_e):
                            pass  # 忽略相同内容错误
                        else:
                            raise edit_e
                # 每3秒发送typing
                if typing_task is None or typing_task.done():
                    typing_task = asyncio.create_task(send_periodic_typing(context.bot, chat_id, 3))
            
            if typing_task:
                typing_task.cancel()
            
            # 检查响应是否为空
            if not full_response or not full_response.strip():
                logger.warning(f"危机模式 AI 返回空响应 (用户 {chat_id})")
                if message:
                    await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)
                await safe_send_message(context.bot, chat_id, API_ERROR_MESSAGE + "\n\n" + CRISIS_RESOURCES, ParseMode.HTML)
                return
            
            # 检查是否为违规警告
            if "⚠️ 警告" in full_response and "违规内容" in full_response:
                add_warning(chat_id)
                new_warning_count = warning_count + 1
                logger.warning(f"用户 {chat_id} AI检测违规警告: {new_warning_count}")
                if new_warning_count >= 5:
                    await safe_send_message(context.bot, chat_id, "🚫 您已被拉黑5次警告，无法继续使用。")
                    # 更新用户为banned
                    # 假设有update_user_banned函数
                # 完成消息（警告）
                if full_response != last_sent:
                    try:
                        await context.bot.edit_message_text(chat_id=chat_id, message_id=message.message_id, text=full_response)
                    except Exception as edit_e:
                        if "Message is not modified" in str(edit_e):
                            pass
                        else:
                            raise edit_e
            else:
                if full_response != last_sent:
                    try:
                        await context.bot.edit_message_text(chat_id=chat_id, message_id=message.message_id, text=full_response)
                    except Exception as edit_e:
                        if "Message is not modified" in str(edit_e):
                            pass
                        else:
                            raise edit_e
                save_message(chat_id, "assistant", full_response)
                append_chat_log(chat_id, "assistant", full_response)
        except asyncio.TimeoutError:
            logger.error(f"危机模式 AI 响应超时 (用户 {chat_id})")
            if message:
                await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)
                await safe_send_message(context.bot, chat_id, API_ERROR_MESSAGE + "\n\n" + CRISIS_RESOURCES, ParseMode.HTML)
        except Exception as e:
            logger.error(f"危机模式 AI 错误: {e}")
            if message:
                await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)
                await safe_send_message(context.bot, chat_id, API_ERROR_MESSAGE + "\n\n" + CRISIS_RESOURCES, ParseMode.HTML)
        return

    # --- 正常聊天模式 ---
    history.append({"role": "user", "content": user_text})
    
    # 保持会话历史在一定长度内
    if len(history) > MAX_HISTORY_LENGTH * 2:
        history = history[-MAX_HISTORY_LENGTH * 2:]

    # 构建包含违规检查的系统提示
    violation_instruction = """
在生成响应前，内部检查用户最后一条消息是否包含违规内容（暴力、邪教、色情、政治敏感等）。如果是，立即输出以下警告消息并停止生成更多内容：
"⚠️ 警告：请避免发送违规内容（暴力、邪教、色情）。继续将导致拉黑。"
如果不是违规，正常生成响应。
"""
    system_prompt_with_violation = SYSTEM_PROMPT + violation_instruction

    # 获取 AI 回复（流式，集成违规检查）
    logger.info(f"生成 AI 响应中... (用户 {chat_id})")
    full_response = ""
    message = None
    typing_task = None
    user = get_user(chat_id)
    warning_count = user.get('warning_count', 0) if user is not None else 0  # 假设数据库有warning_count字段
    try:
        # 发送初始消息
        initial_text = "正在思考中..."
        message = await context.bot.send_message(chat_id=chat_id, text=initial_text)
        last_sent = initial_text
        async for chunk in get_ai_stream(history, system_prompt=system_prompt_with_violation):
            if "错误" in chunk:
                raise Exception(chunk)
            full_response += chunk
            if full_response != last_sent:
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=message.message_id, text=full_response)
                    last_sent = full_response
                except Exception as edit_e:
                    if "Message is not modified" in str(edit_e):
                        pass  # 忽略相同内容错误
                    else:
                        raise edit_e
            # 每3秒发送typing
            if typing_task is None or typing_task.done():
                typing_task = asyncio.create_task(send_periodic_typing(context.bot, chat_id, 3))
        
        if typing_task:
            typing_task.cancel()
        
        # 检查响应是否为空
        if not full_response or not full_response.strip():
            logger.warning(f"AI 返回空响应 (用户 {chat_id})")
            if message:
                await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)
            error_msg = API_ERROR_MESSAGE + "\n\n💡 可能原因：AI模型返回空响应，请稍后重试。"
            await safe_send_message(context.bot, chat_id, error_msg, ParseMode.HTML)
            return
        
        # 检查是否为违规警告
        if "⚠️ 警告" in full_response and "违规内容" in full_response:
            add_warning(chat_id)
            new_warning_count = warning_count + 1
            logger.warning(f"用户 {chat_id} AI检测违规警告: {new_warning_count}")
            if new_warning_count >= 5:
                await safe_send_message(context.bot, chat_id, "🚫 您已被拉黑5次警告，无法继续使用。")
                # 更新用户为banned
                # 假设有update_user_banned函数
            # 完成消息（警告）
            if full_response != last_sent:
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=message.message_id, text=full_response)
                except Exception as edit_e:
                    if "Message is not modified" in str(edit_e):
                        pass
                    else:
                        raise edit_e
        else:
            if full_response != last_sent:
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=message.message_id, text=full_response)
                except Exception as edit_e:
                    if "Message is not modified" in str(edit_e):
                        pass
                    else:
                        raise edit_e
            logger.info(f"AI 响应生成成功 (用户 {chat_id}): {full_response[:50]}...")
            save_message(chat_id, "assistant", full_response)
            append_chat_log(chat_id, "assistant", full_response)
            
            # 心理状态评估（非流式）
            try:
                assessment_history = history + [{"role": "assistant", "content": full_response}]
                assessment_prompt = MENTAL_ASSESSMENT_PROMPT.format(history=str(assessment_history))
                assessment_response = await asyncio.wait_for(get_ai_response([{"role": "system", "content": assessment_prompt}]), timeout=20.0)
                import json
                if assessment_response is not None:
                    assessment = json.loads(assessment_response)
                    update_mental_scores(chat_id, assessment.get('depression', 0), assessment.get('anxiety', 0))
                    logger.info(f"心理评估更新 (用户 {chat_id}): 抑郁={assessment.get('depression', 0)}, 焦虑={assessment.get('anxiety', 0)}")
            except Exception as e:
                logger.warning(f"心理评估失败: {e}")
    except asyncio.TimeoutError:
        logger.error(f"AI 响应超时 (用户 {chat_id})")
        if message:
            await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)
            error_msg = API_ERROR_MESSAGE + "\n\n💡 可能原因：网络问题或 API 限额。请稍后重试，或检查配置。"
            await safe_send_message(context.bot, chat_id, error_msg, ParseMode.HTML)
    except Exception as e:
        logger.error(f"AI 生成错误: {e} (用户 {chat_id})")
        if message:
            await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)
            error_msg = API_ERROR_MESSAGE + "\n\n💡 可能原因：网络问题或 API 限额。请稍后重试，或检查配置。"
            await safe_send_message(context.bot, chat_id, error_msg, ParseMode.HTML)


async def send_periodic_typing(bot, chat_id: int, interval: int):
    """周期性发送 typing 动作"""
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action='typing')
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass

def main() -> None:
    """启动机器人"""
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
    
    # 启动调度器线程
    threading.Thread(target=run_scheduler, daemon=True).start()
    
    # 启动机器人
    _init_and_start_bot()
    
    try:
        # 保持主线程运行
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到退出信号，正在停止机器人...")
        if application:
            application.stop_running()
        logger.info("机器人已停止运行")

if __name__ == '__main__':
    main()
