import json
import logging
import re
import time
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

import requests

from .console import create_live_stream, format_timestamp, log_message


ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "llm_config.json"
DEFAULT_LLM_LOG_PATH = ROOT_DIR / "data" / "logs" / "llm.log"
DEFAULT_WEB_SEARCH_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_WEB_SEARCH_ENDPOINT = "/web_search"
DEFAULT_RETRY_MIN_MAX_TOKENS = 4096
DEFAULT_RETRY_MAX_TOKENS = 8192
DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 3
DEFAULT_LOG_MAX_BODY_CHARS = 20000
DEFAULT_REASONING_STREAM_TITLE = "思维链："
MASKED_VALUE = "***REDACTED***"
DEFAULT_SYSTEM_PROMPT = (
    "你是一个答题助手。"
    "只关心给出最终答案，不要输出解释、分析过程、思维链、推理摘要、参考来源或额外文字。"
    "如果是单选题，仅输出一个选项字母。"
    "如果是多选题，仅输出所有正确选项字母，用英文逗号分隔并按字母顺序排列。"
    "如果是判断题，仅输出“对”或“错”。"
)
DEFAULT_WEB_SEARCH_PROMPT_TEMPLATE = """以下是与题目相关的联网搜索结果：
======
{search_result}
======
请仅将这些搜索结果作为辅助参考。如果搜索结果与题目无关或不足以判断，请忽略它们，并仍然严格按照原要求作答。

{query}"""


class ColoredTimestampFormatter(logging.Formatter):
    def format(self, record):
        message = super().format(record)
        if not message.startswith("["):
            return message

        closing_index = message.find("]")
        if closing_index < 0:
            return message

        timestamp = message[1:closing_index]
        colored_timestamp = format_timestamp(timestamp, color=True)
        return f"{colored_timestamp}{message[closing_index + 1:]}"


def _resolve_log_level(level_name):
    if not isinstance(level_name, str):
        return logging.INFO
    return getattr(logging, level_name.upper(), logging.INFO)


def _resolve_log_path(path_value):
    path = Path(path_value) if path_value else DEFAULT_LLM_LOG_PATH
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sanitize_log_value(value, max_chars):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in {"authorization", "api_key"}:
                sanitized[key] = MASKED_VALUE
            else:
                sanitized[key] = _sanitize_log_value(item, max_chars)
        return sanitized

    if isinstance(value, list):
        return [_sanitize_log_value(item, max_chars) for item in value]

    if isinstance(value, tuple):
        return tuple(_sanitize_log_value(item, max_chars) for item in value)

    if isinstance(value, str) and max_chars > 0 and len(value) > max_chars:
        truncated_length = len(value) - max_chars
        return f"{value[:max_chars]}...(truncated {truncated_length} chars)"

    return value


def _build_llm_logger(log_config):
    if not log_config.get("enabled", True):
        return None

    logger = logging.getLogger("zhihuishu_auto_answer.llm")
    logger.setLevel(_resolve_log_level(log_config.get("level", "INFO")))
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%m-%d %H:%M:%S",
    )

    if log_config.get("console", True):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(
            ColoredTimestampFormatter(
                "[%(asctime)s] [%(levelname)s] %(message)s",
                datefmt="%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(stream_handler)

    log_path = _resolve_log_path(log_config.get("path"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=_safe_int(log_config.get("max_bytes"), DEFAULT_LOG_MAX_BYTES),
        backupCount=_safe_int(log_config.get("backup_count"), DEFAULT_LOG_BACKUP_COUNT),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


class LLMClient:
    def __init__(self, config):
        llm_config = config.get("llm", {})
        self.base_url = llm_config.get("base_url", "").rstrip("/")
        self.chat_endpoint = llm_config.get("chat_endpoint", "/chat/completions")
        self.api_key = llm_config.get("api_key", "").strip()
        self.model = llm_config.get("model", "").strip()
        self.timeout = llm_config.get("timeout", 60)
        self.system_prompt = llm_config.get("system_prompt", DEFAULT_SYSTEM_PROMPT).strip()
        self.request_defaults = config.get("request", {})
        self.extra_headers = llm_config.get("headers", {})
        self.web_search_config = config.get("tools", {}).get("web_search", {})
        self.log_config = config.get("logging", {})
        self.session = requests.Session()
        self.prompt_cache = {}
        self.web_search_warning_shown = False
        self.stream_warning_shown = False
        self.tool_stream_warning_shown = False
        self.stream_supported = True
        self.tool_stream_supported = True
        self.log_max_body_chars = _safe_int(
            self.log_config.get("max_body_chars"),
            DEFAULT_LOG_MAX_BODY_CHARS,
        )
        self.logger = _build_llm_logger(self.log_config)

        if not self.base_url:
            raise ValueError("llm_config.json 缺少 llm.base_url。")
        if not self.model:
            raise ValueError("llm_config.json 缺少 llm.model。")
        if not self.api_key or self.api_key == "YOUR_API_KEY":
            raise ValueError("请先在 llm_config.json 中填写有效的 llm.api_key。")

    def _is_zhipu_llm(self):
        return "bigmodel.cn" in self.base_url

    def _supports_zhipu_web_search_in_chat(self):
        return self._is_zhipu_llm() and self.model.lower().startswith("glm")

    def _warn_once(self, message, flag_name="web_search_warning_shown", event_type="warning"):
        if getattr(self, flag_name, False):
            return
        log_message(message)
        self._log_event(event_type, level=logging.WARNING, message=message)
        setattr(self, flag_name, True)

    def _log_event(self, event_type, level=logging.INFO, **payload):
        if not self.logger:
            return

        message = json.dumps(
            _sanitize_log_value({"event": event_type, **payload}, self.log_max_body_chars),
            ensure_ascii=False,
            indent=2,
        )
        self.logger.log(level, message)

    @staticmethod
    def _get_response_body(response):
        try:
            return response.json()
        except ValueError:
            return response.text

    def _log_request(self, event_type, request_id, attempt, url, payload):
        self._log_event(
            event_type,
            request_id=request_id,
            attempt=attempt,
            method="POST",
            url=url,
            payload=payload,
        )

    def _log_response_payload(
        self,
        event_type,
        request_id,
        attempt,
        url,
        status_code,
        ok,
        elapsed_ms,
        body,
    ):
        self._log_event(
            event_type,
            request_id=request_id,
            attempt=attempt,
            method="POST",
            url=url,
            status_code=status_code,
            ok=ok,
            elapsed_ms=round(elapsed_ms, 2),
            body=body,
        )

    def _log_response(self, event_type, request_id, attempt, url, response, elapsed_ms):
        self._log_response_payload(
            event_type,
            request_id,
            attempt,
            url,
            response.status_code,
            response.ok,
            elapsed_ms,
            self._get_response_body(response),
        )

    def _log_request_error(self, event_type, request_id, attempt, url, elapsed_ms, error):
        self._log_event(
            event_type,
            level=logging.ERROR,
            request_id=request_id,
            attempt=attempt,
            method="POST",
            url=url,
            elapsed_ms=round(elapsed_ms, 2),
            error=str(error),
        )

    def _get_web_search_mode(self):
        if not self.web_search_config.get("enabled", False):
            return "disabled"

        mode = self.web_search_config.get("mode", "auto")
        if mode == "auto":
            if self._supports_zhipu_web_search_in_chat():
                return "chat_tool"
            return "standalone"

        return mode

    def _get_web_search_api_key(self):
        api_key = self.web_search_config.get("api_key", "").strip()
        if api_key and api_key != "YOUR_ZHIPU_API_KEY":
            return api_key
        if self._is_zhipu_llm():
            return self.api_key
        return ""

    def _build_headers(self):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.extra_headers)
        return headers

    def _build_tools(self):
        if not self.web_search_config.get("enabled", False):
            return None, None

        if self._get_web_search_mode() != "chat_tool":
            return None, None

        options = dict(self.web_search_config.get("options", {}))
        options.setdefault("enable", True)

        tools = [{"type": "web_search", "web_search": options}]
        tool_choice = self.web_search_config.get("tool_choice")
        return tools, tool_choice

    def _build_payload(self, query, overrides=None):
        payload = {
            key: value
            for key, value in self.request_defaults.items()
            if value is not None
        }
        if overrides:
            payload.update(overrides)
        payload["model"] = self.model
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": query})
        payload["messages"] = messages

        tools, tool_choice = self._build_tools()
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

        return payload

    def _build_stream_payload(self, payload):
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        if (
            self.tool_stream_supported
            and self._is_zhipu_llm()
            and stream_payload.get("tools")
        ):
            stream_payload["tool_stream"] = True
        return stream_payload

    def _post_chat(self, payload, request_id=None, attempt=1, stream=False):
        url = f"{self.base_url}{self.chat_endpoint}"
        request_id = request_id or str(uuid4())
        self._log_request("llm_request", request_id, attempt, url, payload)
        started_at = time.perf_counter()
        try:
            response = self.session.post(
                url,
                headers=self._build_headers(),
                json=payload,
                timeout=self.timeout,
                stream=stream,
            )
        except requests.RequestException as exc:
            self._log_request_error(
                "llm_request_error",
                request_id,
                attempt,
                url,
                time.perf_counter() - started_at,
                exc,
            )
            raise

        elapsed_ms = time.perf_counter() - started_at
        response._zhihuishu_request_id = request_id
        response._zhihuishu_attempt = attempt
        response._zhihuishu_url = url
        response._zhihuishu_elapsed_ms = elapsed_ms
        if not stream or not response.ok:
            self._log_response(
                "llm_response",
                request_id,
                attempt,
                url,
                response,
                elapsed_ms,
            )
        return response

    @staticmethod
    def _extract_choice(response_data):
        choices = response_data.get("choices", [])
        if not choices:
            raise RuntimeError(f"LLM API 响应缺少 choices: {response_data}")
        return choices[0]

    def _build_web_search_headers(self):
        api_key = self._get_web_search_api_key()
        if not api_key:
            return None
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _extract_search_query(query):
        text = query.strip()
        if "题目：" in text and "你的答案：" in text:
            text = text.split("题目：", 1)[1].split("你的答案：", 1)[0].strip()

        text = " ".join(text.split())
        return text[:70]

    def _build_web_search_payload(self, query):
        options = dict(self.web_search_config.get("options", {}))
        payload = {
            "search_query": self._extract_search_query(query),
            "search_engine": options.get("search_engine", "search_pro"),
            "search_intent": options.get("search_intent", False),
            "count": options.get("count", 5),
            "search_recency_filter": options.get("search_recency_filter", "noLimit"),
            "content_size": options.get("content_size", "medium"),
            "request_id": options.get("request_id") or str(uuid4()),
        }

        for key in ("search_domain_filter", "user_id"):
            value = options.get(key)
            if value:
                payload[key] = value

        return payload

    def _post_web_search(self, query):
        headers = self._build_web_search_headers()
        if not headers:
            self._warn_once(
                "web_search 已启用，但当前并未配置独立的智谱 API Key；已跳过联网搜索。"
            )
            return None

        base_url = self.web_search_config.get("base_url", DEFAULT_WEB_SEARCH_BASE_URL).rstrip("/")
        endpoint = self.web_search_config.get("endpoint", DEFAULT_WEB_SEARCH_ENDPOINT)
        url = f"{base_url}{endpoint}"
        payload = self._build_web_search_payload(query)
        request_id = payload.get("request_id") or str(uuid4())
        self._log_request("web_search_request", request_id, 1, url, payload)
        log_message("正在联网搜索...")
        started_at = time.perf_counter()
        try:
            response = self.session.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            self._log_request_error(
                "web_search_request_error",
                request_id,
                1,
                url,
                time.perf_counter() - started_at,
                exc,
            )
            raise

        self._log_response(
            "web_search_response",
            request_id,
            1,
            url,
            response,
            time.perf_counter() - started_at,
        )
        if not response.ok:
            self._warn_once(f"web_search 请求失败，已跳过联网搜索: {response.text}")
            return None

        return response.json()

    @staticmethod
    def _stringify(value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    def _format_search_results(self, search_results):
        lines = []
        for index, item in enumerate(search_results, start=1):
            title = LLMClient._stringify(item.get("title"))
            content = LLMClient._stringify(item.get("content"))
            link = LLMClient._stringify(item.get("link"))
            media = LLMClient._stringify(item.get("media"))
            publish_date = LLMClient._stringify(item.get("publish_date"))

            block = [f"[ref_{index}] {title or '未命名结果'}"]
            if media or publish_date:
                block.append(f"来源: {media} {publish_date}".strip())
            if link:
                block.append(f"链接: {link}")
            if content:
                block.append(f"摘要: {content}")
            lines.append("\n".join(block))

        return "\n\n".join(lines)

    def _inject_web_search_context(self, query):
        mode = self._get_web_search_mode()
        if mode != "standalone":
            return query

        search_response = self._post_web_search(query)
        if not search_response:
            return query

        search_results = search_response.get("search_result", [])
        if not search_results:
            return query

        template = self.web_search_config.get(
            "prompt_template",
            DEFAULT_WEB_SEARCH_PROMPT_TEMPLATE,
        )
        return template.format(
            query=query,
            search_result=self._format_search_results(search_results),
        )

    def _prepare_query(self, query):
        cached_query = self.prompt_cache.get(query)
        if cached_query is not None:
            return cached_query

        prepared_query = self._inject_web_search_context(query)
        self.prompt_cache[query] = prepared_query
        return prepared_query

    @staticmethod
    def _extract_text_segments(value):
        if isinstance(value, str):
            return [value]

        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str):
                return [text]
            return []

        if isinstance(value, list):
            parts = []
            for item in value:
                parts.extend(LLMClient._extract_text_segments(item))
            return parts

        return []

    @staticmethod
    def _extract_stream_text(value):
        return "".join(LLMClient._extract_text_segments(value))

    @staticmethod
    def _extract_text_content(message):
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, (dict, list)):
            parts = LLMClient._extract_text_segments(content)
            return "\n".join(part.strip() for part in parts if part and part.strip()).strip()

        return ""

    @staticmethod
    def _extract_reasoning_content(message):
        reasoning_content = message.get("reasoning_content", "")
        if isinstance(reasoning_content, str):
            return reasoning_content.strip()
        if isinstance(reasoning_content, (dict, list)):
            parts = LLMClient._extract_text_segments(reasoning_content)
            return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
        return ""

    @staticmethod
    def _extract_tool_calls(message):
        tool_calls = message.get("tool_calls", [])
        if isinstance(tool_calls, list):
            return [item for item in tool_calls if isinstance(item, dict)]
        if isinstance(tool_calls, dict):
            return [tool_calls]
        return []

    @staticmethod
    def _is_web_search_tool_call(tool_call):
        tool_type = LLMClient._stringify(tool_call.get("type")).lower()
        if tool_type == "web_search":
            return True
        if isinstance(tool_call.get("web_search"), dict):
            return True

        function = tool_call.get("function", {})
        if not isinstance(function, dict):
            return False

        function_name = LLMClient._stringify(function.get("name")).lower()
        return "web_search" in function_name or function_name.endswith("search")

    @staticmethod
    def _choice_contains_web_search_signal(choice):
        for key in ("delta", "message"):
            payload = choice.get(key, {})
            if not isinstance(payload, dict):
                continue
            for tool_call in LLMClient._extract_tool_calls(payload):
                if LLMClient._is_web_search_tool_call(tool_call):
                    return True
        return False

    @staticmethod
    def _iter_sse_data_lines(response):
        pending_payload = ""
        for raw_line in response.iter_lines(decode_unicode=False):
            if raw_line is None:
                continue

            if isinstance(raw_line, bytes):
                line = raw_line.decode("utf-8", errors="replace").strip("\r")
            else:
                line = str(raw_line).strip("\r")

            if not line:
                if pending_payload:
                    yield pending_payload
                    pending_payload = ""
                continue

            if line.startswith(":"):
                continue

            if line.startswith("data:"):
                payload = line[5:].lstrip()
                if payload == "[DONE]":
                    if pending_payload:
                        yield pending_payload
                        pending_payload = ""
                    yield payload
                    continue

                if pending_payload:
                    pending_payload = f"{pending_payload}\n{payload}"
                else:
                    pending_payload = payload

                # 有些兼容服务不会严格发送 SSE 空行分隔，这里在 JSON 完整时提前产出。
                try:
                    json.loads(pending_payload)
                except ValueError:
                    continue
                yield pending_payload
                pending_payload = ""

        if pending_payload:
            yield pending_payload

    @staticmethod
    def _is_stream_response(response):
        content_type = response.headers.get("Content-Type", "")
        return "text/event-stream" in content_type.lower()

    @staticmethod
    def _should_retry_with_temperature_one(response_text, payload):
        temperature = payload.get("temperature")
        if temperature in (None, 1, 1.0):
            return False

        text = response_text.lower()
        return "invalid temperature" in text and "only 1 is allowed" in text

    @staticmethod
    def _should_retry_for_truncated_response(choice, content):
        return not content and choice.get("finish_reason") == "length"

    @staticmethod
    def _get_retry_max_tokens(payload):
        current = payload.get("max_tokens")
        if isinstance(current, int) and current > 0:
            return min(max(current * 2, DEFAULT_RETRY_MIN_MAX_TOKENS), DEFAULT_RETRY_MAX_TOKENS)
        return DEFAULT_RETRY_MIN_MAX_TOKENS

    @staticmethod
    def _normalize_answer(answer):
        answer = answer.strip().upper().replace("，", ",")
        answer = re.sub(r"\s+", "", answer)
        if answer in {"对", "错"}:
            return answer
        if re.fullmatch(r"[A-D](?:,[A-D])*", answer):
            parts = []
            for item in answer.split(","):
                if item not in parts:
                    parts.append(item)
            return ",".join(parts)
        return ""

    def _extract_answer_from_reasoning(self, reasoning_content):
        if not reasoning_content:
            return ""

        patterns = [
            r"(?:最终答案|答案|所以答案|因此答案|故答案)[：:\s]*([A-D](?:\s*[，,]\s*[A-D])*)",
            r"(?:最终答案|答案|所以答案|因此答案|故答案)[：:\s]*([对错])",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, reasoning_content, flags=re.IGNORECASE)
            if not matches:
                continue
            candidate = self._normalize_answer(matches[-1])
            if candidate:
                return candidate

        lines = [line.strip("：:。；;，, ") for line in reasoning_content.splitlines() if line.strip()]
        for line in reversed(lines):
            candidate = self._normalize_answer(line)
            if candidate:
                return candidate

        return ""

    def _parse_response(self, response_data):
        choice = self._extract_choice(response_data)
        message = choice.get("message", {})
        return {
            "choice": choice,
            "content": self._extract_text_content(message),
            "reasoning_content": self._extract_reasoning_content(message),
        }

    @staticmethod
    def _should_retry_without_stream(response_text):
        text = response_text.lower()
        if "stream" not in text:
            return False

        unsupported_markers = (
            "not support",
            "unsupported",
            "unknown",
            "invalid",
            "not allowed",
            "不支持",
            "未知",
            "非法",
            "无效",
        )
        return any(marker in text for marker in unsupported_markers)

    @staticmethod
    def _should_retry_without_tool_stream(response_text):
        text = response_text.lower()
        if "tool_stream" not in text:
            return False

        unsupported_markers = (
            "not support",
            "unsupported",
            "unknown",
            "invalid",
            "not allowed",
            "不支持",
            "未知",
            "非法",
            "无效",
        )
        return any(marker in text for marker in unsupported_markers)

    def _log_stream_response_body(self, response, body):
        self._log_response_payload(
            "llm_response",
            getattr(response, "_zhihuishu_request_id", ""),
            getattr(response, "_zhihuishu_attempt", 1),
            getattr(response, "_zhihuishu_url", ""),
            response.status_code,
            response.ok,
            getattr(response, "_zhihuishu_elapsed_ms", 0),
            body,
        )

    def _emit_reasoning_content(self, reasoning_content):
        if not reasoning_content:
            return

        reasoning_stream = create_live_stream(DEFAULT_REASONING_STREAM_TITLE)
        try:
            reasoning_stream.write(reasoning_content)
        finally:
            reasoning_stream.finish()

    def _consume_stream_response(self, response):
        content_parts = []
        reasoning_parts = []
        finish_reason = None
        last_event = {}
        last_usage = None
        search_status_shown = False
        reasoning_stream = create_live_stream(DEFAULT_REASONING_STREAM_TITLE)

        try:
            for data_line in self._iter_sse_data_lines(response):
                if data_line == "[DONE]":
                    break

                try:
                    event = json.loads(data_line)
                except ValueError as exc:
                    raise RuntimeError(f"无法解析流式响应片段: {data_line}") from exc

                if isinstance(event, dict):
                    last_event = event
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        last_usage = usage

                choices = event.get("choices", [])
                if not choices:
                    continue

                choice = choices[0]
                finish_reason = choice.get("finish_reason") or finish_reason

                if not search_status_shown and self._choice_contains_web_search_signal(choice):
                    log_message("正在联网搜索...")
                    search_status_shown = True

                delta = choice.get("delta", {})
                if not isinstance(delta, dict):
                    delta = choice.get("message", {})
                    if not isinstance(delta, dict):
                        delta = {}

                reasoning_chunk = self._extract_stream_text(
                    delta.get("reasoning_content", delta.get("reasoning", delta.get("thinking", "")))
                )
                if reasoning_chunk:
                    reasoning_parts.append(reasoning_chunk)
                    reasoning_stream.write(reasoning_chunk)

                content_chunk = self._extract_stream_text(delta.get("content", ""))
                if content_chunk:
                    content_parts.append(content_chunk)
        finally:
            reasoning_stream.finish()
            response.close()

        response_data = {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {
                        "content": "".join(content_parts),
                        "reasoning_content": "".join(reasoning_parts),
                    },
                }
            ]
        }
        for key in ("id", "model", "created", "request_id"):
            value = last_event.get(key)
            if value is not None:
                response_data[key] = value
        if last_usage:
            response_data["usage"] = last_usage

        self._log_stream_response_body(response, response_data)
        return self._parse_response(response_data)

    def _execute_non_stream_chat_attempt(self, payload, request_id, attempt):
        response = self._post_chat(payload, request_id=request_id, attempt=attempt)
        if not response.ok:
            response_text = response.text
            response.close()
            return {"ok": False, "text": response_text}

        response_data = response.json()
        response.close()
        parsed = self._parse_response(response_data)
        if parsed["reasoning_content"]:
            self._emit_reasoning_content(parsed["reasoning_content"])
        return {"ok": True, "parsed": parsed}

    def _execute_stream_chat_attempt(self, payload, request_id, attempt):
        stream_payload = self._build_stream_payload(payload)
        response = self._post_chat(
            stream_payload,
            request_id=request_id,
            attempt=attempt,
            stream=True,
        )
        if not response.ok:
            response_text = response.text
            response.close()
            if stream_payload.get("tool_stream") and self._should_retry_without_tool_stream(
                response_text
            ):
                self.tool_stream_supported = False
                self._warn_once(
                    "当前模型服务不支持 tool_stream，已回退为普通流式输出；联网搜索状态可能无法实时显示。",
                    flag_name="tool_stream_warning_shown",
                    event_type="llm_tool_stream_warning",
                )
                return self._execute_stream_chat_attempt(payload, request_id, attempt)

            if self._should_retry_without_stream(response_text):
                self.stream_supported = False
                self._warn_once(
                    "当前模型服务不支持 stream，已回退为非流式响应；无法实时输出思维链。",
                    flag_name="stream_warning_shown",
                    event_type="llm_stream_warning",
                )
                return self._execute_non_stream_chat_attempt(payload, request_id, attempt)

            return {"ok": False, "text": response_text}

        if self._is_stream_response(response):
            return {
                "ok": True,
                "parsed": self._consume_stream_response(response),
            }

        response_data = response.json()
        response.close()
        self._log_stream_response_body(response, response_data)
        parsed = self._parse_response(response_data)
        if parsed["reasoning_content"]:
            self._emit_reasoning_content(parsed["reasoning_content"])
        return {"ok": True, "parsed": parsed}

    def _execute_chat_attempt(self, payload, request_id, attempt):
        log_message("正在思考中...")
        if self.stream_supported:
            return self._execute_stream_chat_attempt(payload, request_id, attempt)
        return self._execute_non_stream_chat_attempt(payload, request_id, attempt)

    def get_response(self, query):
        query = self._prepare_query(query)
        request_id = str(uuid4())
        payload = self._build_payload(query)
        attempt = 1
        result = self._execute_chat_attempt(payload, request_id, attempt)

        if not result["ok"] and self._should_retry_with_temperature_one(result["text"], payload):
            attempt += 1
            self._log_event(
                "llm_retry",
                level=logging.WARNING,
                request_id=request_id,
                attempt=attempt,
                reason="service_only_accepts_temperature_1",
            )
            payload = self._build_payload(query, overrides={"temperature": 1})
            result = self._execute_chat_attempt(payload, request_id, attempt)

        if not result["ok"]:
            raise RuntimeError(f"LLM API 请求失败: {result['text']}")

        parsed = result["parsed"]
        if parsed["content"]:
            return parsed["content"]

        if self._should_retry_for_truncated_response(parsed["choice"], parsed["content"]):
            attempt += 1
            retry_payload = self._build_payload(
                query,
                overrides={"max_tokens": self._get_retry_max_tokens(payload)},
            )
            self._log_event(
                "llm_retry",
                level=logging.WARNING,
                request_id=request_id,
                attempt=attempt,
                reason="empty_content_and_finish_reason_length",
                max_tokens=retry_payload.get("max_tokens"),
            )
            retry_result = self._execute_chat_attempt(retry_payload, request_id, attempt)
            if not retry_result["ok"]:
                raise RuntimeError(f"LLM API 请求失败: {retry_result['text']}")

            parsed = retry_result["parsed"]
            if parsed["content"]:
                return parsed["content"]

        fallback_answer = self._extract_answer_from_reasoning(parsed["reasoning_content"])
        if fallback_answer:
            self._log_event(
                "llm_answer_fallback",
                level=logging.WARNING,
                request_id=request_id,
                attempt=attempt,
                answer=fallback_answer,
                source="reasoning_content",
            )
            return fallback_answer

        raise RuntimeError("LLM API 响应中未提取到文本内容。")


def load_config(config_path=CONFIG_PATH):
    if not config_path.exists():
        raise FileNotFoundError(
            f"未找到配置文件 {config_path}，请先参考 llm_config.example.json 创建 llm_config.json。"
        )

    with config_path.open("r", encoding="utf-8") as file:
        return json.load(file)


@lru_cache(maxsize=1)
def get_config():
    return load_config()


def should_repeat_answers():
    answer_config = get_config().get("answer", {})
    return answer_config.get("repeat_until_duplicate", True)


@lru_cache(maxsize=1)
def get_model():
    return LLMClient(get_config())
