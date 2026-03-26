# Repository Guidelines

## Project Structure & Module Organization
This repository is a small Python automation project built around Playwright, OCR, and an LLM API. The three entry points are `manual_mode.py` for manual mode, `onepage.py` for a single exam page, and `auto_answer_question.py` for walking a full test list. Shared logic lives in the `core/` package: `core/model.py` (LLM client and config), `core/browser_session.py` (browser startup and login-state persistence), `core/question_flow.py` (question answering flow), and `core/answer_context.py` (prompt assembly and course context).

Keep generated assets under `data/`, including screenshots, demo media, cookies, and logs. Use `llm_config.example.json` as the template for local setup; `llm_config.json` is local-only and ignored by Git.

## Build, Test, and Development Commands
Use a local virtual environment and install the minimal dependencies:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Run the main workflows with:

```powershell
python onepage.py
python auto_answer_question.py
python -m py_compile core/__init__.py core/answer_context.py core/browser_session.py core/model.py core/question_flow.py auto_answer_question.py manual_mode.py onepage.py
```

The first two commands are manual smoke tests. The `py_compile` check is the quickest way to catch syntax errors before a commit.

## Coding Style & Naming Conventions
Follow the existing Python style: 4-space indentation, `snake_case` for modules/functions/variables, `PascalCase` for classes, and `UPPER_CASE` for shared constants such as XPath selectors and default config values. Keep entry scripts thin; move reusable browser, OCR, and LLM logic into shared modules instead of duplicating it across scripts.

Prefer `pathlib.Path` for filesystem paths and keep comments brief and targeted to non-obvious Playwright timing or login-state handling behavior.

## Testing Guidelines
There is no dedicated automated test suite yet. Every change should include:

- A syntax check with `python -m py_compile ...`
- A manual run of the affected entry point
- Notes on OCR accuracy, login-cookie reuse, and answer flow when behavior changes

If you add automated tests, place them under `tests/` and use `test_*.py` naming.

## Commit & Pull Request Guidelines
Recent history follows Conventional Commit prefixes with concise Chinese summaries, for example `feat: 支持登录后保存登录状态cookie` and `fix: 修复考试页面题目定位时序问题`. Keep commits focused and describe the user-visible behavior change, not just the implementation.

Pull requests should include a short problem statement, affected scripts/config, manual verification steps, and screenshots or GIFs for browser-flow changes. Never commit real API keys, `llm_config.json`, saved cookies, or log files from `data/logs/`.
