# ai_handler.py
import logging
import os
import requests
import json
from config import OPENROUTER_API_KEY, AI_MODEL, AI_TEMPERATURE
from prompts import SYSTEM_PROMPT
from typing import Optional, AsyncGenerator

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


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
        
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        
        http_referer = os.getenv("HTTP_REFERER")
        if http_referer:
            headers["HTTP-Referer"] = http_referer

        site_name = os.getenv("YOUR_SITE_NAME")
        if site_name:
            headers["X-Title"] = site_name

        data = {
            "model": AI_MODEL,
            "messages": messages,
            "temperature": AI_TEMPERATURE,
        }
        if max_tokens:
            data["max_tokens"] = max_tokens
        
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            data=json.dumps(data)
        )
        
        response.raise_for_status()
        completion = response.json()
        
        if (completion.get("choices") and
            len(completion["choices"]) > 0 and
            (choice := completion["choices"][0]).get("message") is not None and
            (msg := choice["message"]).get("content") is not None):
            response_text = msg["content"]
            logging.info(f"收到 OpenRouter 的回复: {response_text[:100]}...")
            return response_text
        else:
            logging.warning("AI 响应为空或无效")
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
        
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        
        http_referer = os.getenv("HTTP_REFERER")
        if http_referer:
            headers["HTTP-Referer"] = http_referer

        site_name = os.getenv("YOUR_SITE_NAME")
        if site_name:
            headers["X-Title"] = site_name

        data = {
            "model": AI_MODEL,
            "messages": messages,
            "temperature": AI_TEMPERATURE,
            "stream": True,
        }
        if max_tokens:
            data["max_tokens"] = max_tokens
        
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            data=json.dumps(data),
            stream=True
        )
        
        response.raise_for_status()
        
        for chunk in response.iter_lines():
            if chunk:
                decoded_chunk = chunk.decode('utf-8')
                if decoded_chunk.startswith("data: "):
                    data_str = decoded_chunk[6:]
                    if data_str != "[DONE]":
                        try:
                            chunk_data = json.loads(data_str)
                            if chunk_data.get("choices") and len(chunk_data["choices"]) > 0:
                                delta = chunk_data["choices"][0].get("delta", {})
                                if delta.get("content"):
                                    yield delta["content"]
                                    logging.debug(f"流式 chunk: {delta['content']}")
                        except json.JSONDecodeError:
                            continue
        logging.info("流式响应完成")
    except Exception as e:
        logging.error(f"调用 AI 时发生未知错误: {e}")
        yield f"错误: {str(e)}"