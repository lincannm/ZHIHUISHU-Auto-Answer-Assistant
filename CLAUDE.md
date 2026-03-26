# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用开发命令

### 创建 / 使用虚拟环境（可选）

```bash
python -m venv .venv
source .venv/bin/activate  # Windows 下用: .venv/Scripts/activate
```

### 安装依赖

```bash
pip install -r requirements.txt
```

> 说明：当前 `requirements.txt` 只包含运行主流程必需依赖（`requests`, `selenium`, `cnocr`）。如需增加新三方库，请同步更新该文件。

### 运行脚本

主入口脚本都在仓库根目录：

```bash
# 手动模式：只在收到命令时读取当前题目并用 LLM 回答
python manual_mode.py

# 单个测试页自动答题
python onepage.py

# 测试列表页批量自动答题
python auto_answer_question.py
```

运行时会自动读取 `llm_config.json`。仓库中提供 `llm_config.example.json`，实际使用请拷贝后填入自己的配置，真实密钥不要提交。

### 其它说明

- 项目没有测试框架和 lint 配置，也没有 CI 脚本；如果需要新增，请按具体需求自行设计，不要在本文件编造统一命令。

## 代码结构与架构概览

本项目是一个基于「Selenium + OCR + LLM API」的智慧树自动答题脚本，核心是浏览器自动化 + 截图 OCR + 调用统一 LLM 客户端。高层结构可从 `readme.md` 获取，下面是补充说明，方便之后改动：

### 1. 运行模式与入口脚本

- `manual_mode.py`
  - 提供“手动模式” REPL：用户输入 URL，脚本负责复用/获取登录态、识别当前题目文字，并在收到命令时调用 LLM 给出答案。
  - 不自动点击选项或提交，由用户自己在浏览器操作，主要用来人工控制节奏/调试。

- `onepage.py`
  - 面向单个测试页自动答题。
  - 一般流程：打开指定测试页 → 按题目顺序逐题截图 OCR → 调用 LLM → 自动选择答案 → 用户手动确认提交。

- `auto_answer_question.py`
  - 面向“测试列表页”，对多个测试顺序答题。
  - 逻辑上会复用与 `onepage.py` 相同的“题目识别 + LLM 求答 + 自动点击”流程，只是多了一层对测试列表的遍历和进入/退出单个测试页的控制。

这三个入口脚本不直接实现复杂逻辑，而是委托给 `core/` 包中的下层模块。

### 2. 共享模块包 `core/`

所有非入口的业务模块都放在 `core/` 包中，入口脚本通过 `from core.xxx import ...` 引用，包内模块之间使用相对导入。

#### 2.1 浏览器与登录态管理

- `core/browser_session.py`
  - 负责 Selenium WebDriver 初始化（浏览器类型、窗口大小、超时配置等）。
  - 统一处理智慧树登录态 cookie 的「保存 / 恢复」，实际文件路径为：
    - `data/zhihuishu_cookies.json`（已写入 .gitignore）。
  - 启动流程通常是：
    1. 创建 WebDriver
    2. 尝试从本地 cookie 文件恢复登录态
    3. 若恢复失败或 cookie 失效，则让用户在打开的浏览器里手动登录，登录成功后再把 cookie 写回本地

对 Selenium 相关行为（比如切换窗口、等待元素、截图等）的通用封装，也应该放在这里，而不是入口脚本里到处散落。

#### 2.2 题目流程与共享逻辑

- `core/question_flow.py`
  - 封装了“读取题目 → OCR 识别 → 调用 LLM → 解析答案 → 自动答题”的共享流程，供 `manual_mode.py` / `onepage.py` / `auto_answer_question.py` 复用。
  - 典型职责：
    - 根据当前页面结构与题目区域定位规则，对题目区域进行截图；
    - 使用 `cnocr` 识别文字，包括题干、选项、课程名称等；
    - 控制是否只“看题”（只 OCR 不问 LLM）、还是“答题”（OCR 后调用 LLM 返回答案）；
    - 处理多次调用 LLM 并找出重复答案的逻辑（对应 `llm_config.json` 中 `answer.repeat_until_duplicate`）；
    - 根据识别出的答案，在 Selenium 页面上匹配并点击正确选项；
    - 对 OCR/解析失败、LLM 返回空内容、max_tokens 不足等情况进行必要的重试或兜底。

修改页面结构适配（例如智慧树改版导致题目在不同的 DOM 或截图区域），一般都在这里集中调整，而不是分别改三个入口脚本。

#### 2.3 统一 LLM 客户端与配置

- `core/model.py`
  - 提供统一的 `LLMClient` / 配置加载逻辑，是整个项目与各类大模型服务交互的抽象层。
  - 职责包含：
    - 从 `llm_config.json` 读取配置：`llm`、`request`、`answer`、`logging`、`tools.web_search` 等字段；
    - 构造兼容 OpenAI 风格 `chat/completions` 的 HTTP 请求（包含 `base_url`、`chat_endpoint`、`model`、`api_key` 等）；
    - 根据配置决定是否启用智谱 `web_search` 工具：
      - GLM 模型：优先使用 chat tool
      - 非 GLM 模型：自动走“独立 web_search API + 把结果注入 prompt”的两段式流程
    - 统一处理温度、top_p、max_tokens 等推理参数，以及重试逻辑；
    - 写入 LLM / web_search 请求与响应日志到 `data/logs/llm.log`，并在必要时做文本截断和密钥脱敏。

- `llm_config.example.json`
  - 提供完整的配置字段示例与说明，关键点已在 `readme.md` 里写明。
  - 实际使用时，一般拷贝为 `llm_config.json` 并填入真实 API Key 等。

项目中不再有按模型提供商拆分的 `LLMs/*.py`，所有模型调用路径都应走 `core/model.py`，避免在业务代码里直接拼 HTTP 请求。

### 3. 日志与数据文件

- LLM 和联网搜索日志：`data/logs/llm.log`
- 智慧树登录 cookie：`data/zhihuishu_cookies.json`

这些路径在 `readme.md` 中已有说明，并全部被 `.gitignore` 忽略。实现/改动与这些文件有关的逻辑时，务必：
- 不把真实密钥、cookie 或完整日志加入版本控制；
- 在日志中继续遵循现有的脱敏策略（对 `api_key` / `Authorization` 等字段做脱敏处理）。

## 对未来 Claude Code 实例的建议

- 在修改逻辑前，优先查阅 `readme.md`，那是项目行为与配置的权威说明；
- 如需扩展功能（比如增加新的答题模式），优先考虑复用并扩展 `core/browser_session.py` 与 `core/question_flow.py`，而不是在入口脚本里写大量重复代码；
- 与 LLM/联网搜索有关的改动，统一集中在 `core/model.py` 与 `llm_config.example.json`，保持业务层仅依赖抽象的 LLM 客户端与少量配置字段。
