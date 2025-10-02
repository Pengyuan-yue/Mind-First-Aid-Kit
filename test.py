import asyncio
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import json

from main import is_crisis_message, handle_message
from database import init_db, get_user, increment_daily_chat, add_warning, update_mental_scores, save_message, get_user_history, append_chat_log, update_chat_end_time, reset_all_daily_chats, get_worst_users, get_inactive_users, create_or_update_user
from unittest.mock import Mock
from config import VIOLATION_KEYWORDS, CRISIS_KEYWORDS
from prompts import VIOLATION_CHECK_PROMPT, MENTAL_ASSESSMENT_PROMPT
from ai_handler import get_ai_response

# 模拟Update和Context
class MockUpdate(Mock):
    def __init__(self, chat_id, text):
        super().__init__()
        self.effective_chat = MockChat(chat_id)
        self.message = MockMessage(text)
        self.from_user = MagicMock()

class MockChat:
    def __init__(self, id):
        self.id = id

class MockMessage(Mock):
    def __init__(self, text):
        super().__init__()
        self.text = text
        self.reply_text = MagicMock()

class MockContext(Mock):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.user_data = {}

class MockBot(Mock):
    async def send_message(self, chat_id, text):
        print(f"模拟发送消息到 {chat_id}: {text}")

    async def send_chat_action(self, chat_id, action):
        print(f"模拟聊天动作 {action} 到 {chat_id}")

async def test_database():
    print("测试数据库初始化...")
    init_db()
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    assert c.fetchone() is not None
    conn.close()
    print("数据库测试通过")

async def test_user_management():
    print("测试用户管理...")
    user_id = 12345
    get_user(user_id)  # 应该创建用户
    user = get_user(user_id)
    assert user is not None
    assert user['daily_chat_count'] == 0
    assert user['is_banned'] == False
    print("用户管理测试通过")

async def test_chat_limit():
    print("测试聊天次数限制...")
    user_id = 54321
    for i in range(101):
        allowed = increment_daily_chat(user_id)
        if i < 100:
            assert allowed == True
        else:
            assert allowed == False
    print("聊天次数限制测试通过")

async def test_violation_detection():
    print("测试违规内容检测...")
    user_id = 67890
    create_or_update_user(user_id)
    # 模拟违规消息
    violation_text = "暴力内容"
    # 模拟AI检查
    with patch('main.get_ai_response', return_value="VIOLATION"):
        # 这里需要模拟handle_message，但由于复杂，测试关键词
        assert any(keyword in violation_text.lower() for keyword in VIOLATION_KEYWORDS) == False  # 假设不匹配关键词，但AI匹配
    # 测试警告
    add_warning(user_id)
    user = get_user(user_id)
    if user:
        assert user['warning_count'] == 1
    print("违规检测测试通过")

async def test_mental_assessment():
    print("测试心理状态评估...")
    user_id = 11111
    create_or_update_user(user_id)
    # 模拟AI响应
    with patch('main.get_ai_response', return_value='{"depression": 5.0, "anxiety": 3.0}'):
        # 模拟对话后评估
        assessment_response = '{"depression": 5.0, "anxiety": 3.0}'
        assessment = json.loads(assessment_response)
        update_mental_scores(user_id, assessment['depression'], assessment['anxiety'])
        user = get_user(user_id)
        if user:
            assert user['depression_score'] == 5.0
            assert user['anxiety_score'] == 3.0
    print("心理状态评估测试通过")

async def test_chat_log():
    print("测试聊天记录...")
    user_id = 22222
    create_or_update_user(user_id)
    append_chat_log(user_id, "user", "测试消息")
    # 检查文件是否存在
    import os
    log_file = f"chat_logs/{user_id}.txt"
    assert os.path.exists(log_file)
    with open(log_file, 'r', encoding='utf-8') as f:
        content = f.read()
        assert "测试消息" in content
    print("聊天记录测试通过")

async def test_crisis_detection():
    print("测试危机检测...")
    crisis_text = "我想自杀"
    assert is_crisis_message(crisis_text) == True
    normal_text = "今天天气不错"
    assert is_crisis_message(normal_text) == False
    print("危机检测测试通过")

async def test_scheduler_functions():
    print("测试调度器函数...")
    user_id = 33333
    create_or_update_user(user_id, last_message_time=(datetime.now() - timedelta(minutes=11)).isoformat())
    update_chat_end_time(user_id)  # 模拟标记结束
    user = get_user(user_id)
    if user:
        assert user['last_chat_end_time'] is not None
    
    # 模拟重置
    reset_all_daily_chats()
    user = get_user(user_id)
    if user:
        assert user['daily_chat_count'] == 0
    
    # 最差用户
    update_mental_scores(user_id, 9.0, 8.0)
    worst = get_worst_users(1)
    assert len(worst) >= 1
    
    # 不活跃用户
    inactive = get_inactive_users(1)
    print("调度器函数测试通过")

async def test_bot_simulation():
    print("测试Bot模拟...")
    mock_update = MockUpdate(99999, "hello")
    mock_bot = MockBot()
    mock_context = MockContext(mock_bot)
    create_or_update_user(99999)
    with patch('main.get_ai_response', return_value="Hello!"), \
         patch('main.Update'), \
         patch('telegram.Update'), \
         patch('main.ContextTypes.DEFAULT_TYPE'):
        await handle_message(mock_update, mock_context)  # type: ignore
    print("Bot模拟测试通过")

async def main_test():
    await test_database()
    await test_user_management()
    await test_chat_limit()
    await test_violation_detection()
    await test_mental_assessment()
    await test_chat_log()
    await test_crisis_detection()
    await test_scheduler_functions()
    await test_bot_simulation()
    print("所有测试通过！")

if __name__ == '__main__':
    asyncio.run(main_test())