# 智慧树自动答题助手

## 项目简介

这是一个基于 `Selenium + OCR + LLM API` 的智慧树自动答题脚本。

当前版本已经移除原来按模型提供商拆分的 `LLMs/*.py` 实现，改成统一的配置文件和统一的 API 客户端：

- 所有模型参数都放在 `llm_config.json`
- 所有对话请求都通过 `model.py` 中的统一 `LLMClient` 发送
- 已接入智谱官方 `web_search` 能力，可在配置文件中开启或关闭
- 非 GLM 模型会自动改走“先调用智谱 Web Search API，再把结果注入目标模型提示词”的两段式流程

## 工作流程

1. 读取 `llm_config.json`
2. 初始化统一的 LLM 客户端
3. Selenium 打开智慧树页面
4. 用户手动登录后继续
5. 脚本对题目区域截图
6. `cnocr` 识别题目文字
7. 将题目发送给 LLM
8. LLM 多次生成答案，最先重复出现的答案被视为最终答案
9. Selenium 将答案点击回网页
10. 用户手动确认提交

## 安装

```bash
pip install -r requirements.txt
```

## 配置

先编辑 `llm_config.json`。

默认配置已经切到智谱官方接口，并开启了联网搜索能力：

```json
{
  "llm": {
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "chat_endpoint": "/chat/completions",
    "api_key": "YOUR_API_KEY",
    "model": "glm-4-air",
    "system_prompt": "你是一个答题助手。只关心给出最终答案，不要输出解释、分析过程、思维链、推理摘要、参考来源或额外文字。如果是单选题，仅输出一个选项字母。如果是多选题，仅输出所有正确选项字母，用英文逗号分隔并按字母顺序排列。如果是判断题，仅输出“对”或“错”。",
    "timeout": 60
  },
  "request": {
    "temperature": 1,
    "top_p": 0.95,
    "max_tokens": 4096
  },
  "tools": {
    "web_search": {
      "enabled": true,
      "mode": "auto",
      "api_key": "YOUR_ZHIPU_API_KEY",
      "base_url": "https://open.bigmodel.cn/api/paas/v4",
      "endpoint": "/web_search",
      "tool_choice": "auto",
      "prompt_template": "以下是与题目相关的联网搜索结果：\n======\n{search_result}\n======\n请仅将这些搜索结果作为辅助参考。如果搜索结果与题目无关或不足以判断，请忽略它们，并仍然严格按照原要求作答。\n\n{query}",
      "options": {
        "search_engine": "search_pro",
        "count": 5,
        "search_recency_filter": "noLimit",
        "content_size": "medium"
      },
      "render": {
        "result_limit": 3,
        "title_max_chars": 80,
        "content_max_chars": 220,
        "include_link": false
      }
    }
  }
}
```

### 字段说明

- `llm.base_url`: 模型服务地址
- `llm.chat_endpoint`: 对话补全接口路径
- `llm.api_key`: API Key
- `llm.model`: 模型名
- `llm.system_prompt`: 系统级约束，用来强制模型只输出最终答案
- `llm.timeout`: 请求超时时间，单位秒
- `request`: 统一请求参数，会直接合并到请求体
- `tools.web_search.enabled`: 是否启用智谱联网搜索工具
- `tools.web_search.mode`: `auto` / `chat_tool` / `standalone`
- `tools.web_search.api_key`: 独立的智谱 API Key；非智谱模型使用联网搜索时必须配置
- `tools.web_search.base_url`: 智谱 Web Search API 地址
- `tools.web_search.endpoint`: 智谱 Web Search API 路径
- `tools.web_search.tool_choice`: 工具选择策略，通常用 `auto`
- `tools.web_search.prompt_template`: 独立搜索模式下，如何把搜索结果注入到最终提问中
- `tools.web_search.options`: 智谱 Web Search API 的配置项
- `request.temperature`: 某些模型只接受 `1`，如果服务端报温度参数错误，优先改成 `1`
- `request.max_tokens`: 推理模型如果频繁出现 `finish_reason = length`，需要适当调大

### 切换其他模型提供商

如果目标平台兼容 OpenAI 风格的 `chat/completions` 接口，一般只需要改这几个字段：

- `llm.base_url`
- `llm.chat_endpoint`
- `llm.api_key`
- `llm.model`

联网搜索模式的行为如下：

- `mode = auto`: GLM 模型优先走 chat tool；非 GLM 模型自动走独立的智谱 Web Search API
- `mode = chat_tool`: 仅适用于智谱 GLM 对话请求
- `mode = standalone`: 永远先调用智谱 Web Search API，再把结果拼接进提示词

如果你使用的是非智谱模型，但仍希望接入智谱联网搜索，必须额外配置 `tools.web_search.api_key` 为智谱的 API Key。

## 运行

### 单个测试页

```bash
python onepage.py
```

### 测试列表页

```bash
python auto_answer_question.py
```

运行后只需要输入页面 URL。模型信息不再通过命令行交互输入，而是统一从 `llm_config.json` 读取。

## 文件说明

- `onepage.py`: 对单个测试页答题
- `auto_answer_question.py`: 对测试列表页中的所有测试顺序答题
- `model.py`: 统一 LLM 客户端和配置加载逻辑
- `llm_config.json`: 实际使用的模型配置
- `llm_config.example.json`: 配置模板

## 注意事项

- 页面题目区域仍然依赖 OCR，识别错误会直接影响答题结果
- 智慧树页面部分元素处于 `shadow-root (closed)` 中，因此当前方案仍然依赖截图识别
- 联网搜索会提升信息覆盖面，但也可能让模型更容易输出解释性文本，建议按实际效果调整 `prompt_template`
- 推理模型若返回空 `content` 且 `finish_reason = length`，当前代码会自动放大 `max_tokens` 重试一次，并在必要时尝试从推理结果中提取最终答案
- `llm_config.json` 已加入 `.gitignore`，避免误提交真实密钥

## 参考文档

- 智谱联网搜索工具文档: https://docs.bigmodel.cn/cn/guide/tools/web-search
