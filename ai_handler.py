# ai_handler.py
import logging
from openai import AsyncOpenAI, APIError
from config import OPENROUTER_API_KEY, AI_MODEL, AI_TEMPERATURE
from prompts import SYSTEM_PROMPT
from typing import Optional, AsyncGenerator

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# 初始化 OpenAI 客户端，指向 OpenRouter
client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

async def get_ai_response(history: list, system_prompt: str = SYSTEM_PROMPT, max_tokens: Optional[int] = None) -> Optional[str]:
    """
    调用 OpenRouter API 获取非流式 AI 回复。
    
    Args:
        history: 对话历史列表。
        system_prompt: 当前场景下的系统提示词。
        max_tokens: 最大输出 token 数（可选，默认 None 表示无限制）。
 
    Returns:
        AI 的回复文本，如果出错则返回 None。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        *history
    ]

    try:
        logging.info(f"向 OpenRouter 发送非流式请求，模型: {AI_MODEL}, 历史长度: {len(history)}")
        import os
        extra_headers = {}
        http_referer = os.getenv("HTTP_REFERER")
        if http_referer:
            extra_headers["HTTP-Referer"] = http_referer
        site_name = os.getenv("SITE_NAME")
        if site_name:
            extra_headers["X-Title"] = site_name
        kwargs = {
            "extra_headers": extra_headers,
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
    
    Args:
        history: 对话历史列表。
        system_prompt: 当前场景下的系统提示词。
        max_tokens: 最大输出 token 数（可选，默认 None 表示无限制）。
 
    Yields:
        逐步 yield 内容块。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        *history
    ]

    try:
        logging.info(f"向 OpenRouter 发送流式请求，模型: {AI_MODEL}, 历史长度: {len(history)}")
        import os
        extra_headers = {}
        http_referer = os.getenv("HTTP_REFERER")
        if http_referer:
            extra_headers["HTTP-Referer"] = http_referer
        site_name = os.getenv("SITE_NAME")
        if site_name:
            extra_headers["X-Title"] = site_name
        kwargs = {
            "extra_headers": extra_headers,
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