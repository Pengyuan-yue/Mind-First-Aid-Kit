# config.py
import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

# --- Telegram 配置 ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- OpenRouter AI 配置 ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AI_MODEL = "x-ai/grok-4-fast:free" 
AI_TEMPERATURE = 0.6  

# --- 会话管理 ---
MAX_HISTORY_LENGTH = 10  

# --- 心理危机处理协议 ---
CRISIS_KEYWORDS = [
    "想死", "自杀", "自残", "了结", "结束一切", "没希望了", "撑不住了",
    "伤害自己", "割腕", "吃药", "跳楼", "上吊", "活不下去", "不想活了",
    "我现在就要", "我准备好了", "再见"
]

# --- 违规内容关键词 ---
VIOLATION_KEYWORDS = [
    # 暴力相关
    "杀", "打死", "虐待", "伤害他人", "恐怖主义", "爆炸", "枪击",
    # 邪教相关 (避免宗教)
    "邪教", "洗脑", "邪门歪道", "非法组织", "极端主义",
    # 色情相关 (避免艺术/生理)
    "色情", "裸体", "性交", "性虐", "成人内容", "黄片"
]

# 外部危机资源（请替换为本地化、经过验证的资源）
CRISIS_RESOURCES = """
🆘 <b>请立即寻求专业帮助，你不是一个人在战斗：</b>

1.  <b>希望24热线 (全国心理危机研究与干预中心)</b>
    • 电话: <code>400-161-9995</code>

2.  <b>北京心理危机研究与干预中心</b>
    • 电话: <code>010-8295-1332</code>

3.  <b>上海市精神卫生中心</b>
    • 电话: <code>021-12320-5</code>

4.  <b>紧急情况请直接拨打</b>
    • 医疗急救: <code>120</code>
    • 报警: <code>110</code>

<b>记住，此刻的痛苦是可以被理解和帮助的。请务必联系他们。</b>
"""