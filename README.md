# AIChat · API 对话工作台

一个面向专业人士的桌面端 API 对话客户端。所有请求参数、上下文组成、最终报文全部可见可编辑，方便调试各家大模型接口。

- 纯 Python + PyQt6，无额外运行时依赖
- 支持多种协议：OpenAI Chat Completions / OpenAI Responses / Anthropic Messages / Google Gemini / 自定义 HTTP 模板
- **系统提示词默认为空**：留空时请求报文中完全不出现 `system` 字段

## 功能特性

### 模型管理
- 任意添加 / 复制 / 删除 API 模型档案，支持导入导出 JSON
- 每个档案可单独配置 Base URL、端点路径覆盖、API Key、鉴权方式（Bearer / x-api-key / api-key / URL 查询参数 / 不携带）、代理、超时、重试、TLS 校验
- 内置「测试连通性」，直接看到 HTTP 状态、延迟与返回片段
- 支持透传任意服务商私有字段：`extra_headers` / `extra_query` / `extra_body`（深合并进请求体）

### 请求参数
- 采样参数：`temperature`、`top_p`、`top_k`、`max_tokens`、`min_tokens`、`presence_penalty`、`frequency_penalty`、`repetition_penalty`、`seed`、`stop`、`n`、`logprobs`、`top_logprobs`
- 思维强度：统一开关 + 强度档位（minimal / low / medium / high）+ 自定义预算 tokens，并自动映射到各家字段（`reasoning_effort` / `thinking.budget_tokens` / `thinkingConfig.thinkingBudget`）
- **空值即不发送**：任何留空的参数都不会写进请求体，避免污染报文
- 常用参数直接展开，低频/复杂项收进折叠分组，界面保持清爽

### 可编辑上下文（核心）
「检查器 → 上下文 → 上下文编辑」页把本次要发送的上下文完整摊开：

- 逐条 **勾选启用 / 禁用**（取消勾选只影响本次请求，不动本地历史）
- **增删条目、上下移动调整顺序**
- **行内编辑**内容，或在下方编辑器中编辑完整原文
- 一键 **从会话重建**：按「参数 → 上下文裁剪策略」重新生成整份草稿
- 自动把新产生的消息并入草稿，同时保留你的手工修改
- 草稿随会话持久化，重启后仍在

配套的裁剪策略（完整 / 最近 N 条 / Token 预算，支持历史思维链内联回传）决定「从会话重建」时默认启用哪些消息。

### 完全可见
- **最终请求体**页：原样展示即将发送的 JSON
- **请求元信息**页：方法、URL、请求头（密钥默认掩码，可一键揭示）、查询参数、流式模式
- **请求日志**：每次请求记录状态码、延迟、首字时间、usage、原始 SSE 流、请求体与响应体，可逐条回看
- 状态栏实时显示上下文 token 估算；请求返回后用服务端 usage 自动校准估算系数

## 环境要求

- Windows 10/11（其他平台理论上可用，未做打包脚本）
- Python 3.10+（开发环境使用 3.14）
- 依赖见 [`requirements.txt`](requirements.txt)

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 运行
python main.py

# 可选：指定数据目录
python main.py --data-dir D:\aichat-data
```

## 构建免安装版

```bash
pip install pyinstaller
python build.py            # 生成 dist\AIChat\ 并打包为 dist\AIChat-windows-x64.zip
python build.py --no-zip   # 只构建，不压缩
```

Windows 下也可直接双击 `build.bat`。

## 数据存放位置

| 平台 | 目录 |
| --- | --- |
| Windows | `%APPDATA%\AIChat\` |
| macOS | `~/Library/Application Support/AIChat/` |
| Linux | `~/.config/AIChat/` |

- `config.json`：模型档案、默认参数、界面设置（明文 JSON，便于直接查看和版本管理）
- `aichat.db`：会话、消息、请求日志（SQLite）

## 项目结构

```
aichat/
├── main.py                  启动入口
├── build.py / build.bat     发布包构建脚本
├── requirements.txt
└── app/
    ├── models.py            数据模型（模型档案 / 请求参数 / 消息 / 会话 / 请求记录）
    ├── config.py            配置读写（模型库、默认参数、界面设置）
    ├── storage.py           SQLite 持久层（会话 / 消息 / 请求日志）
    ├── protocol.py          协议适配器（5 种协议，编译报文 + 解析响应）
    ├── client.py            请求执行引擎（SSE / NDJSON 流式解析、计时、原始流记录）
    ├── workers.py           请求工作线程
    ├── tokenizer.py         轻量 token 估算（CJK 感知 + 服务端校准）
    ├── context.py           上下文组装、裁剪策略、可编辑草稿
    └── ui/
        ├── theme.py         深色主题与样式表
        ├── widgets.py       可复用组件（折叠分组）
        ├── main_window.py   主窗口
        ├── model_dialog.py  模型管理对话框
        ├── params_panel.py  参数面板
        ├── context_panel.py 上下文编辑器与报文预览
        ├── chat_view.py     对话区与输入框
        └── log_panel.py     请求日志与请求详情
```

## 自定义 HTTP 协议

当服务商接口不在内置协议覆盖范围内时，可用「自定义 HTTP」完全自定义报文：

```
占位符：{{model}} {{base_url}} {{api_key}} {{system}} {{stream}} {{messages}} {{params}}
· {{key}}  替换为字符串值
· {{#key}} 替换为 JSON 原文（用于数组 / 对象 / 布尔）
```

再配合「正文路径 / 流式增量路径 / 用量路径」（如 `choices[0].delta.content`）即可解析任意返回结构。

## 许可

[MIT](LICENSE)