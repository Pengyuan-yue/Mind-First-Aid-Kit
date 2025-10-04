import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

DB_PATH = 'database.db'

def init_db():
    """初始化数据库和表结构"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 用户表
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            daily_chat_count INTEGER DEFAULT 0,
            warning_count INTEGER DEFAULT 0,
            depression_score REAL DEFAULT 0,
            anxiety_score REAL DEFAULT 0,
            is_in_crisis INTEGER DEFAULT 0,
            last_active_time TEXT,
            is_banned INTEGER DEFAULT 0,
            last_chat_end_time TEXT,
            last_message_time TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 消息历史表（用于存储聊天记录，便于评估）
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    conn.commit()
    conn.close()

def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """获取用户数据"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0],
            'daily_chat_count': row[1],
            'warning_count': row[2],
            'depression_score': row[3],
            'anxiety_score': row[4],
            'is_in_crisis': bool(row[5]),
            'last_active_time': row[6],
            'is_banned': bool(row[7]),
            'last_chat_end_time': row[8],
            'last_message_time': row[9]
        }
    return None

def create_or_update_user(user_id: int, **kwargs) -> None:
    """创建或更新用户数据"""
    user = get_user(user_id)
    now = datetime.now().isoformat()
    
    if user is None:
        # 新用户
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO users (user_id, is_in_crisis, last_active_time, last_message_time, updated_at)
            VALUES (?, 0, ?, ?, ?)
        ''', (user_id, now, now, now))
        conn.commit()
        conn.close()
    else:
        # 更新现有用户
        updates = []
        values = []
        for key, value in kwargs.items():
            if key in ['depression_score', 'anxiety_score', 'daily_chat_count', 'warning_count', 'is_banned', 'is_in_crisis']:
                updates.append(f"{key} = ?")
                values.append(value)
            elif key in ['last_active_time', 'last_chat_end_time', 'last_message_time']:
                updates.append(f"{key} = ?")
                values.append(value)
        if updates:
            values.append(user_id)
            values.append(now)
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute(f'''
                UPDATE users SET {', '.join(updates)}, updated_at = ?
                WHERE user_id = ?
            ''', values)
            conn.commit()
            conn.close()

def increment_daily_chat(user_id: int) -> bool:
    """增加每日聊天次数，返回是否超过限制"""
    user = get_user(user_id)
    if user and user['is_banned']:
        return False  # 已拉黑，不允许
    new_count = (user['daily_chat_count'] + 1 if user else 1)
    create_or_update_user(user_id, daily_chat_count=new_count)
    return new_count <= 100

def reset_daily_chat(user_id: int) -> None:
    """重置每日聊天次数（每日0点）"""
    create_or_update_user(user_id, daily_chat_count=0)

def add_warning(user_id: int) -> int:
    """添加警告，返回警告次数"""
    user = get_user(user_id)
    new_count = (user['warning_count'] + 1 if user else 1)
    is_banned = new_count >= 3
    create_or_update_user(user_id, warning_count=new_count, is_banned=is_banned)
    return new_count

def update_mental_scores(user_id: int, depression: float, anxiety: float) -> None:
    """更新心理分数"""
    create_or_update_user(user_id, depression_score=depression, anxiety_score=anxiety, last_active_time=datetime.now().isoformat())

def save_message(user_id: int, role: str, content: str) -> None:
    """保存消息到数据库"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO messages (user_id, role, content, timestamp)
        VALUES (?, ?, ?, ?)
    ''', (user_id, role, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_user_history(user_id: int, limit: int = 20) -> list:
    """获取用户最近历史消息"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT role, content, timestamp FROM messages
        WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
    ''', (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return [{'role': row[0], 'content': row[1]} for row in reversed(rows)]  # 逆序恢复时间线

def get_worst_users(limit: int = 3) -> list:
    """获取心理状态最差的用户（基于综合分数）"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT user_id, (depression_score + anxiety_score) as total_score
        FROM users
        WHERE is_banned = 0
        ORDER BY total_score DESC LIMIT ?
    ''', (limit,))
    rows = c.fetchall()
    conn.close()
    return [{'user_id': row[0], 'total_score': row[1]} for row in rows]

def update_chat_end_time(user_id: int) -> None:
    """更新聊天结束时间"""
    create_or_update_user(user_id, last_chat_end_time=datetime.now().isoformat())

def get_inactive_users(hours: int = 3) -> list:
    """获取聊天结束3小时后的用户，用于发送问候"""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT user_id FROM users
        WHERE last_chat_end_time < ? AND is_banned = 0
    ''', (cutoff,))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

# 每日重置函数（可定时调用）
def reset_all_daily_chats():
    """每日重置所有用户的聊天次数"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET daily_chat_count = 0')
    conn.commit()
    conn.close()

def append_chat_log(user_id: int, role: str, content: str) -> None:
    """追加聊天记录到txt文件"""
    import os
    # 确保 chat_logs 目录存在
    os.makedirs("chat_logs", exist_ok=True)
    log_file = f"chat_logs/{user_id}.txt"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {role}: {content}\n")