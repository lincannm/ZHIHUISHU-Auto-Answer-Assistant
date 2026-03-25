import json
import re
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import requests


ROOT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = ROOT_DIR / "llm_config.json"
DEFAULT_WEB_SEARCH_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_WEB_SEARCH_ENDPOINT = "/web_search"
DEFAULT_RETRY_MIN_MAX_TOKENS = 4096
DEFAULT_RETRY_MAX_TOKENS = 8192
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
        self.session = requests.Session()
        self.prompt_cache = {}
        self.web_search_warning_shown = False

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

    def _warn_once(self, message):
        if self.web_search_warning_shown:
            return
        print(message)
        self.web_search_warning_shown = True

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

    def _post_chat(self, payload):
        return self.session.post(
            f"{self.base_url}{self.chat_endpoint}",
            headers=self._build_headers(),
            json=payload,
            timeout=self.timeout,
        )

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
        response = self.session.post(
            f"{base_url}{endpoint}",
            headers=headers,
            json=self._build_web_search_payload(query),
            timeout=self.timeout,
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
    def _extract_text_content(message):
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue

                if not isinstance(item, dict):
                    continue

                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            return "\n".join(part.strip() for part in parts if part and part.strip()).strip()

        return ""

    @staticmethod
    def _extract_reasoning_content(message):
        reasoning_content = message.get("reasoning_content", "")
        if isinstance(reasoning_content, str):
            return reasoning_content.strip()
        if isinstance(reasoning_content, list):
            parts = []
            for item in reasoning_content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
        return ""

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

    def get_response(self, query):
        query = self._prepare_query(query)
        payload = self._build_payload(query)
        response = self._post_chat(payload)

        if not response.ok and self._should_retry_with_temperature_one(response.text, payload):
            payload = self._build_payload(query, overrides={"temperature": 1})
            response = self._post_chat(payload)

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"LLM API 请求失败: {response.text}") from exc

        response_data = response.json()
        parsed = self._parse_response(response_data)
        if parsed["content"]:
            return parsed["content"]

        if self._should_retry_for_truncated_response(parsed["choice"], parsed["content"]):
            retry_payload = self._build_payload(
                query,
                overrides={"max_tokens": self._get_retry_max_tokens(payload)},
            )
            retry_response = self._post_chat(retry_payload)
            try:
                retry_response.raise_for_status()
            except requests.HTTPError as exc:
                raise RuntimeError(f"LLM API 请求失败: {retry_response.text}") from exc

            response_data = retry_response.json()
            parsed = self._parse_response(response_data)
            if parsed["content"]:
                return parsed["content"]

        fallback_answer = self._extract_answer_from_reasoning(parsed["reasoning_content"])
        if fallback_answer:
            return fallback_answer

        raise RuntimeError(f"LLM API 响应中未提取到文本内容: {response_data}")


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
