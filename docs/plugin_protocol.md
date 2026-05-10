# Fiam 功能插件协议

这份协议只管“功能性接入”：能收发、感知、操作、通知用户的外部能力。网页、git diff、dashboard、flow、Pool、recall 这类基础设施不算插件。

## 一句话模型

新增功能 = 新建 `plugins/<id>/plugin.toml` + 一个边缘 bridge/client。核心 daemon 只认两个动作：

- 入站：插件把消息发布到 `fiam/receive/<source>`，daemon 写入 flow。
- 出站：AI 写 marker，如 `[→email:Iris] ...`，marker registry 解析后发布到 `fiam/dispatch/<target>`。

禁用功能 = 把 manifest 里的 `enabled = false`。已接入运行时会在 daemon 入站、Conductor 出站、bridge 启动/发送时一起检查这个开关。

## Manifest 字段

每个插件目录只有一个必需文件：`plugins/<id>/plugin.toml`。

```toml
id = "email"
name = "Email bridge"
enabled = true
status = "active"
kind = "channel"
description = "Long-lived asynchronous channel for low-frequency conversations."
transports = ["mqtt", "imap", "smtp"]
capabilities = ["receive_text", "dispatch_text"]
receive_sources = ["email"]
dispatch_targets = ["email"]
entrypoint = "scripts/bridges/bridge_email.py"
auth = "FIAM_EMAIL_USER / FIAM_EMAIL_PASS plus fiam.toml comms.email_*"
latency = "polling; default 600s inbound, dispatch is immediate SMTP"
env = ["FIAM_EMAIL_USER", "FIAM_EMAIL_PASS"]
replaces = []
notes = []
```

约定：

- `receive_sources` 是入站 topic 叶子名，例如 `favilla` 对应 `fiam/receive/favilla`。
- `dispatch_targets` 是出站 marker 和 topic 叶子名，例如 `[→email:Iris]` 对应 `fiam/dispatch/email`。
- `enabled = false` 表示核心系统不接收该 source，也不向该 target 派发。
- `status` 只描述阶段：`active`、`legacy_keep`、`planned`、`merge_into_app`、`as_needed` 等。

## 当前插件边界

- `favilla`：选中文本批注采集，可以并入手机 app。
- `email`：长期保留，适合作为低频、耐久异步通道。
- `xiao`：独立外设；现有代码目录叫 `channels/limen`，manifest 把 `xiao/limen` 都注册为 source/target。
- `app`：未来主入口。它不是另一个记忆系统，只是更舒服的 capture/chat/dispatch 客户端。
- `voice-call`：电话能力，独立插件，便于单独做 consent 和日志规则。
- `device-control`：手机/电脑操控，必须是本地 agent + allowlist，不能给裸 shell。
- `ring`：智能指环，健康/生物数据默认只存派生信号。
- `mcp`：工具桥，不是人格层，也不是后台 hook。

## 鉴权和网络

推荐分两层边界：

- 私域：dashboard、Conductor 控制台、内部 API 走 Tailscale。手机、电脑、服务器在 tailnet 内，公网零暴露。
- 公域：真正要给朋友或公开网页用的子域走 Cloudflare Tunnel + Cloudflare Access，邮箱白名单或 passkey。
- MQTT：默认只绑 `127.0.0.1:1883`；跨机器时也只走 tailnet，不直接暴露公网。
- HTTP capture：短期继续用 `FIAM_INGEST_TOKEN`；自制 app 应升级成设备绑定 token，必要时再做 token rotation。

## 延迟预期

- 自制 app/web：HTTP/WebSocket 到服务器通常亚秒级；如果要系统通知，移动端 push 平台可能引入数秒到分钟级不确定性。
- Favilla：一次 HTTP capture + 本机 MQTT publish，tailnet 内通常亚秒级。
- XIAO/Limen：直连 MQTT 在 LAN/tailnet 内是毫秒到百毫秒；如果设备深度睡眠，唤醒会额外增加秒级延迟。
- Email：轮询型，email 默认 600 秒。dispatch 是即时 SMTP 调用。

## Web、PWA、本地 App 的区别

- Web：部署轻、更新快、权限窄。适合 dashboard、标注、chat、capture 表单。
- PWA：在 Web 基础上加桌面图标、缓存、部分通知能力，是手机端第一阶段最省事的形态。
- Tauri/Electron：适合桌面封装。Tauri 更轻，Electron 生态更成熟；只有需要文件系统、本地通知、托盘、快捷键时才值得做。
- 原生手机 app：适合分享意图、通知、后台任务、传感器、BLE。Favilla 的 PROCESS_TEXT/SEND 能力最终应并进这里。

因此当前前端封装成“软件”的工作量：PWA 很小；桌面 Tauri 中等；完整原生手机 app 最大，但它能统一 Favilla、通知和本机能力。

## MCP 到底是什么

MCP 是 Model Context Protocol：让 AI 客户端以统一方式调用外部工具、读资源、取 prompt。它解决的是“AI 主动查/调用工具”的协议问题，不解决“把所有上下文偷偷注入 Claude app”的问题。

对 Fiam 来说：

- Claude Code：有 hooks、JSONL、命令行，因此能被 Fiam 深度接入。
- Claude API：我们控制请求，可以把 identity envelope、recall、工具结果放进请求，也可以直接走 Fiam API。
- Claude app：如果不能 hook，它通常只能让 AI 主动通过 MCP/API 查 Fiam；Fiam 不能保证每轮都在后台注入隐藏上下文。

所以“app 无法 hook，只能 AI 主动查吗？”基本是：是的，除非 app 自身提供 MCP/extension/context 注入能力。MCP 可以让 Claude app 查 `self/`、查 recall、查 Pool、发消息，但它不是后台总线。

## 身份意识如何共享

身份不放在 Claude app、Claude Code 或 API 任一端，而放在 Fiam：

- `home/self/`：人格、状态、目标、日记等 AI 自己维护的文字。
- `flow.jsonl`：所有经历留痕。
- `Pool`：经切割后的长期事件图谱。
- `recall.md`：漂移触发的一次性回忆注入。

不同端只是不同 adapter：

- CC adapter：hooks + JSONL + daemon。
- API adapter：请求前组装 identity envelope + recall/tool result。
- App adapter：如果是 Claude app，就通过 MCP 主动查；如果是自制 app，就直接调用 Fiam server，由服务器决定走 API 还是 CC。

## 三端路由

- 去 Claude app：当它是用户正在使用的客户端时，给它 MCP 工具/资源，让它主动查 Fiam。它适合轻量对话和人工在场场景。
- 去自制 app：走 Fiam API，入站发布为 `fiam/receive/app`，出站用 `[→app:Iris]`。服务器可以按任务路由到 Claude API 或唤醒 CC。
- 去 Claude Code：代码、仓库、长任务、需要本机工具和 hooks 的场景走 CC。CC 的输出 marker 再回到插件 dispatch。

一句话：app 是入口，Fiam 是身份和路由层，CC/API 是执行后端。

## 数据存储：自动触发 vs AI 主动查

两类记忆分开：

- 自动触发记忆：flow + FeatureStore + Pool + graph。它是经历的事实层，源头在服务器，适合 git/rclone/备份同步。
- AI 主动查/主动写：`home/self/`、`home/world/`、项目笔记、自己写的解释/计划。它是 AI 的工作知识层，也应在服务器作为源头，并用 git 同步。

不建议把 website 当源头。Website/dashboard 应是展示、编辑、发布层；真正的源头仍是服务器文件 + git 同步。公开网站只发布可公开的投影，私密内容不直接放进去。

## 命令

```bash
python scripts/fiam.py plugin list
python scripts/fiam.py plugin show tg
python scripts/fiam.py plugin disable tg
python scripts/fiam.py plugin enable tg
```

启停 bridge 进程仍由 systemd 或手工运行负责；manifest 决定 Fiam 是否接收/派发该能力。