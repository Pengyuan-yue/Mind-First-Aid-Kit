# ai_handler.py
import logging
import os
# 新增 httpx 导入，用于底层客户端配置
import httpx
from openai import AsyncOpenAI, APIError
from config import OPENROUTER_API_KEY, AI_MODEL, AI_TEMPERATURE
from prompts import SYSTEM_PROMPT
from typing import Optional, AsyncGenerator

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- 核心修改部分：客户端初始化 ---

# 1. 准备自定义请求头
default_headers = {}
http_referer = os.getenv("HTTP_REFERER")
if http_referer:
    default_headers["HTTP-Referer"] = http_referer

site_name = os.getenv("YOUR_SITE_NAME")
if site_name:
    default_headers["X-Title"] = site_name

# 2. 解决服务器代理冲突：
# 创建一个明确禁用系统代理的 httpx 客户端实例，
# 并设置合理的超时时间。
# 这通过将 proxies 设置为 None 或 {} 来阻止 httpx 自动从环境变量中读取代理。
http_client_config = httpx.AsyncClient(
    # 设置 proxies=None 来忽略系统环境变量中的代理配置
    proxies=None,
    # 设置一个合理的超时，以应对 OpenRouter 的慢响应或网络波动
    timeout=httpx.Timeout(timeout=60.0, connect=10.0), 
)

# 3. 初始化 OpenAI 客户端，传入配置好的 httpx 客户端
client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    default_headers=default_headers,
    # 关键：通过 http_client 参数传递我们自定义的 httpx 实例
    http_client=http_client_config,
)

# --- 核心修改部分结束 ---


async def get_ai_response(history: list, system_prompt: str = SYSTEM_PROMPT, max_tokens: Optional[int] = None) -> Optional[str]:
    """
    调用 OpenRouter API 获取非流式 AI 回复。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        *history
    ]

    try:
        logging.info(f"向 OpenRouter 发送非流式请求，模型: {AI_MODEL}, 历史长度: {len(history)}")
        
        kwargs = {
            "model": AI_MODEL,
            "messages": messages,
            "temperature": AI_TEMPERATURE,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        
        completion = await client.chat.completions.create(**kwargs)
        if (completion.choices and
            len(completion.choices) > 0 and
            (choice := completion.choices[0]).message is not None and
            (msg := choice.message).content is not None):
            response_text = msg.content
            logging.info(f"收到 OpenRouter 的回复: {response_text[:100]}...")
            return response_text
        else:
            logging.warning("AI 响应为空或无效")
            return None
    except APIError as e:
        logging.error(f"OpenRouter API 错误: {e}")
        return None
    except Exception as e:
        logging.error(f"调用 AI 时发生未知错误: {e}")
        return None


async def get_ai_stream(history: list, system_prompt: str = SYSTEM_PROMPT, max_tokens: Optional[int] = None) -> AsyncGenerator[str, None]:
    """
    调用 OpenRouter API 获取流式 AI 回复。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        *history
    ]

    try:
        logging.info(f"向 OpenRouter 发送流式请求，模型: {AI_MODEL}, 历史长度: {len(history)}")
        
        kwargs = {
            "model": AI_MODEL,
            "messages": messages,
            "temperature": AI_TEMPERATURE,
            "stream": True,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        
        stream_response = await client.chat.completions.create(**kwargs)
        async for chunk in stream_response:
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
                    logging.debug(f"流式 chunk: {delta.content}")
        logging.info("流式响应完成")
    except APIError as e:
        logging.error(f"OpenRouter API 错误: {e}")
        yield f"错误: {str(e)}"
    except Exception as e:
        logging.error(f"调用 AI 时发生未知错误: {e}")
        yield f"错误: {str(e)}"