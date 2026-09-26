# pdf2zh-next 自动翻译系统（Ausar 定制版）

监视 `OneDrive\文档\Daily_paper`，把新论文自动翻译成中英对照（DeepSeek，默认无思考档），输出到 `Translated/`。
沿用旧 pdf2zh v1 系统的目录约定（marker / 日志 / 输出位置），翻译引擎换为 **pdf2zh-next 2.9.0 + ausar-patches**
（补丁内容：DeepSeek 思考模式修复 + low 档放开，见 fork 仓库的 `ausar-patches` 分支）。

## 目录结构

| 路径 | 说明 |
|---|---|
| `src/` | pdf2zh-next 源码（git，origin = AusarPu/PDFMathTranslate-next 的 ausar-patches 分支） |
| `.venv/` | 运行环境（uv，Python 3.12，editable 安装 src） |
| `config.toml` | 运行配置（**含 API 密钥，勿外传**；模板见 `config.example.toml`） |
| `watch.py` | 监视外壳（`deploy/` 同名文件为版本化存档） |
| `watch.ps1` / `watch.vbs` | 启动链（vbs → ps1 → pythonw watch.py） |
| `watch.lock` | 单例锁（运行时存在，正常） |
| `watch-crash.log` | 崩溃记录（无人值守兜底） |
| `sandbox/` | 实验与测试材料（可清理） |

## 运行

- 启动：运行 `watch.vbs`（由计划任务亦可触发）
- 单例：重复启动会自动退出（`watch.lock` 文件锁）
- 停止：结束对应 `pythonw.exe` 进程
- 常用参数（`watch.py --help` 全部）：
  - `--once` 只扫一轮；`--once --dry-run` 只列候选不翻译
  - `--interval 120` 扫描间隔；`--retry-delays 10,30,60` 重试退避

## config.toml 要点

- 默认档位 `deepseek_thinking_mode = "disabled"`（省钱档，实测质量与思考档无系统性差异）
- 单篇临时换档（命令行覆盖配置）：
  ```
  .venv\Scripts\pdf2zh_next.exe "论文.pdf" --config-file config.toml --deepseek --term-deepseek `
    --deepseek-thinking-mode enabled --deepseek-reasoning-effort low --output Translated
  ```
- 输出：双语 dual、无 mono、无加水印

## 故障排查

- 日志：`Daily_paper\pdf2zh-watch.log`（保留最后 100 行）
- 失败标记：`〈论文名〉.pdf2zh.failed.json` —— **删除该文件**后才会重试
- 崩溃：`watch-crash.log`
- 手动补跑：`watch.py --once`

## 维护

- 升上游：`cd src && git fetch upstream && git rebase upstream/main`（补丁集中在 `pdf2zh_next/config/translate_engine_model.py`）
- 备份：本目录（除 `.venv`/`sandbox`）已随 fork 的 `ausar-patches` 分支的 `deploy/` 目录版本化
