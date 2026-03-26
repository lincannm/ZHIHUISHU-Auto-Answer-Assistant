# 智慧树自动答题助手

## 项目简介

这是一个基于 `Playwright + OCR + LLM API` 的智慧树自动答题脚本。

当前版本已经移除原来按模型提供商拆分的 `LLMs/*.py` 实现，改成统一的配置文件和统一的 API 客户端：

- 所有模型参数都放在 `llm_config.json`
- 所有对话请求都通过 `core/model.py` 中的统一 `LLMClient` 发送
- 已接入智谱官方 `web_search` 能力，可在配置文件中开启或关闭
- 非 GLM 模型会自动改走“先调用智谱 Web Search API，再把结果注入目标模型提示词”的两段式流程

## 工作流程

1. 读取 `llm_config.json`
2. 初始化统一的 LLM 客户端
3. Playwright 尝试恢复本地保存的智慧树登录状态
4. 若 cookie 失效或首次运行，则优先尝试使用配置文件中的手机号/密码自动填充并点击登录；若仍需滑块、短信或图片验证码，再由用户补充完成
5. 脚本对题目区域截图
6. `cnocr` 识别题目文字
7. 将课程名称和题目一起发送给 LLM
8. 根据 `answer.repeat_until_duplicate` 决定是单次生成答案，还是多次生成并以最先重复出现的答案为最终答案
9. Playwright 将答案点击回网页
10. 用户手动确认提交

## 安装

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

浏览器运行说明：

- 默认优先使用本机已安装的 Chrome；如果未检测到，则回退到 Playwright 自带的 Chromium
- 首次使用 Playwright 自带 Chromium 前，需要先执行 `python -m playwright install chromium`
- 也可以通过环境变量 `ZHIHUISHU_CHROME_BINARY` 指定本机 `chrome.exe` 路径

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
  "answer": {
    "repeat_until_duplicate": true
  },
  "logging": {
    "enabled": true,
    "console": true,
    "path": "data/logs/llm.log",
    "level": "INFO",
    "max_bytes": 5242880,
    "backup_count": 3,
    "max_body_chars": 20000
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
  },
  "zhihuishu": {
    "login": {
      "enabled": false,
      "username": "YOUR_PHONE_NUMBER",
      "password": "YOUR_PASSWORD",
      "auto_submit": true
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
- `answer.repeat_until_duplicate`: 是否对同一题反复请求模型，直到某个答案再次出现；设为 `false` 时每题只请求一次
- `logging.enabled`: 是否启用 LLM / web_search 请求日志
- `logging.console`: 是否同步输出到控制台
- `logging.path`: 日志文件路径，默认写入 `data/logs/llm.log`
- `logging.level`: 日志级别，通常使用 `INFO`
- `logging.max_bytes`: 单个日志文件的最大体积，超过后自动轮转
- `logging.backup_count`: 最多保留多少个历史日志文件
- `logging.max_body_chars`: 单条日志中请求/响应文本的最大字符数，超过后截断
- `tools.web_search.enabled`: 是否启用智谱联网搜索工具
- `tools.web_search.mode`: `auto` / `chat_tool` / `standalone`
- `tools.web_search.api_key`: 独立的智谱 API Key；非智谱模型使用联网搜索时必须配置
- `tools.web_search.base_url`: 智谱 Web Search API 地址
- `tools.web_search.endpoint`: 智谱 Web Search API 路径
- `tools.web_search.tool_choice`: 工具选择策略，通常用 `auto`
- `tools.web_search.prompt_template`: 独立搜索模式下，如何把搜索结果注入到最终提问中
- `tools.web_search.options`: 智谱 Web Search API 的配置项
- `zhihuishu.login.enabled`: 是否启用智慧树手机号自动登录
- `zhihuishu.login.username`: 智慧树登录手机号
- `zhihuishu.login.password`: 智慧树登录密码
- `zhihuishu.login.auto_submit`: 自动填充后是否自动点击登录，默认 `true`
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

### 统一入口

```bash
python main.py
```

运行后会先让你交互式选择模式，再输入页面 URL。脚本会打开浏览器并复用本地保存的登录态。

如果当前页面或跳转结果落在 `https://passport.zhihuishu.com/login`，并且 `llm_config.json` 中配置了 `zhihuishu.login.username` / `password`，脚本会自动填充手机号密码，并自动点击“登录”。如果智慧树仍要求滑块、图片验证码或短信验证，终端会提示你在浏览器里补完后继续。

可选模式：

- `1` / `manual` / `手动模式`
- `2` / `onepage` / `单个答题页`
- `3` / `tests` / `答题列表页`

其中手动模式下，脚本不会自动选项、自动切题或自动提交，只会在你输入命令时读取当前正在显示的题目并调用 AI 返回答案。

常用命令：

- `ask` / `答题`: 回答当前正在显示的题目
- `ask <n>` / `答题 <n>`: 仅当当前显示的是第 `n` 题时回答，否则提示你先在浏览器里手动切题
- `show` / `看题`: 只识别当前题目文字，不调用 AI
- `show <n>` / `看题 <n>`: 仅当当前显示的是第 `n` 题时识别
- `list` / `count` / `题数`: 显示当前是第几题、总共有几题
- `course` / `课程`: 显示当前识别到的课程名称
- `help` / `帮助`: 显示命令说明
- `quit` / `exit` / `退出`: 退出手动模式

说明：

- 这个页面通常一次只展开一道题，切题仍然需要你在浏览器里手动操作
- 手动模式仍然依赖 OCR 读取题目区域，OCR 结果可能受页面样式和字体影响

模型信息不再通过命令行交互输入，而是统一从 `llm_config.json` 读取。

首次运行或登录态失效时，脚本会先尝试用配置文件里的手机号密码自动登录；如果自动提交后仍需二次验证，再提示你手动完成。登录成功后会自动把 Playwright 登录状态保存到 `data/zhihuishu_storage_state.json`。下次运行会先尝试恢复这份登录态，能直接进入答题页时就不再需要重复登录。如果本地还保留旧版 `data/zhihuishu_cookies.json`，当前版本也会自动尝试导入一次。

默认会把 LLM 请求、响应、重试和错误同时输出到控制台与 `data/logs/llm.log`。日志中会保留完整 prompt / response（超长内容按 `logging.max_body_chars` 截断），并对 `api_key` / `Authorization` 做脱敏处理。

运行时控制台还会额外显示两类状态提示：

- 触发独立联网搜索，或流式工具调用中检测到 `web_search` 时，会显示 `正在联网搜索...`
- 调用大模型时，会先显示 `正在思考中...`；如果模型返回 `reasoning_content` 并且服务支持流式输出，会实时打印 `思维链：` 内容

## 文件说明

- `main.py`: 统一入口，启动后交互式选择模式并输入 URL
- `manual_mode.py`: 兼容旧用法的薄包装，内部转调统一入口的手动模式
- `onepage.py`: 兼容旧用法的薄包装，内部转调统一入口的单个答题页模式
- `auto_answer_question.py`: 兼容旧用法的薄包装，内部转调统一入口的答题列表页模式
- `core/`: 共享模块包
  - `core/workflows.py`: 统一收口手动模式、单个答题页和答题列表页三种 workflow
  - `core/browser_session.py`: 统一管理 Playwright 浏览器初始化与智慧树登录态的保存/恢复
  - `core/question_flow.py`: 共享的题目识别、OCR、AI 求答和自动答题流程
  - `core/model.py`: 统一 LLM 客户端和配置加载逻辑
  - `core/answer_context.py`: 答题 prompt 构建与课程名称识别
- `llm_config.json`: 实际使用的模型配置
- `llm_config.example.json`: 配置模板
- `data/logs/llm.log`: LLM 与 web_search 的请求/响应日志

## 注意事项

- 页面题目区域仍然依赖 OCR，识别错误会直接影响答题结果
- 智慧树页面部分元素处于 `shadow-root (closed)` 中，因此当前方案仍然依赖截图识别
- 联网搜索会提升信息覆盖面，但也可能让模型更容易输出解释性文本，建议按实际效果调整 `prompt_template`
- 如果模型服务不支持 `stream` 或智谱的 `tool_stream`，代码会自动回退到兼容模式；这种情况下仍能答题，但思维链或联网搜索状态不一定能实时显示
- 推理模型若返回空 `content` 且 `finish_reason = length`，当前代码会自动放大 `max_tokens` 重试一次，并在必要时尝试从推理结果中提取最终答案
- `llm_config.json` 已加入 `.gitignore`，避免误提交真实密钥
- `data/zhihuishu_storage_state.json` 保存的是本地登录态，已加入 `.gitignore`，不要外传
- `data/logs/` 已加入 `.gitignore`，但日志里会包含题目、课程名和模型响应，不要外传

## 参考文档

- 智谱联网搜索工具文档: https://docs.bigmodel.cn/cn/guide/tools/web-search
