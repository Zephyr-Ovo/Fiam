# T2 — Model Catalog + AI 自主路由 + Console 下拉

> **背景**：当前 cc/api 二选一，AI 通过文本暗示「换 api」切换。Zephyr 想要：
> - **AI 自主选 family**（claude / gemini），不指定具体 model 和 provider
> - **Zephyr 在 console 配「家族 → provider+version+fallback 列表」**
> - **Console 下拉**从供应商网站抓真实可用模型（Anthropic /v1/models, Google list_models）
>
> 与 T1/T3 的关系：**完全独立**，由 Copilot 同时在做。T1 改 transcript/carryover/flow（assistant_text_beats、_record_*_app_turn、_append_carryover、_apply_app_control_markers），T3 改 build_api_messages 加 [recent_thoughts]。请避开这些点位。
>
> 仓库：`f:\fiam-code` / 远程 `git@github.com:Zephyr-Ovo/Fiam.git`，main 分支。Push 后我（Copilot）这边 ssh isp 拉取重启。
>
> ISP 部署：`/home/fiet/fiam-code` + `sudo systemctl restart fiam-dashboard.service`。Dashboard 是 Svelte，build 命令 `cd dashboard && npm run build`（产物 `dashboard/build/`，不入 git，ISP 端要重 build）。
>
> 服务结构：API 路径走 `src/fiam/runtime/api.py`（POE OpenAI-compatible），CC 路径走 `scripts/dashboard_server.py` 里的 `_run_cc_favilla_chat` / `_iter_cc_favilla_chat_events`（claude CLI subprocess）。

## 1. fiam.toml schema 扩展

新增 `[catalog]` 段，按 family 组织：

```toml
[catalog.claude]
provider = "poe"             # poe | anthropic | bedrock
model = "Claude-Opus-4.6"    # 当前默认
fallbacks = ["Claude-Sonnet-4.6", "Claude-Haiku-4.5"]
extended_thinking = true
budget_tokens = 32000

[catalog.gemini]
provider = "aistudio"        # aistudio | vertex
model = "gemini-3.1-flash-lite"  # 当前默认（看 /memories/repo/gemini-3.1-flash-lite-switch-2026-05-08.md）
fallbacks = ["gemini-3.0-pro"]   # 之类
extended_thinking = false
```

`src/fiam/config.py` 新增 `Catalog` dataclass + `FiamConfig.catalog: dict[str, Catalog]`，从 toml `[catalog.*]` 段加载。沿用现有 `dataclasses` + 手动 parse 风格（看 config.py 里其他段的写法）。

向后兼容：保留现有 `[api]` / `[daemon]` / `cc_model` 字段，catalog 缺失时 fallback 到老字段。

## 2. AI 路由 marker

新增 `<route family="claude"/>` 或 `<route family="gemini"/>`（匹配 `<wake>` / `<carry_over>` 风格）。

**写在哪**：
- marker 定义：`src/fiam/markers.py`（看 `RouteMarker` / `_CONTROL_MARKERS`）
- 解析：`scripts/dashboard_server.py` 里 `_apply_app_control_markers`（追加一个 family 字段返回）
- 调度：`scripts/dashboard_server.py` 里 `_select_favilla_chat_runtime` 改造——优先看 carryover 里 AI 上一轮发的 family（用 carry_over 机制传递），其次看 attachments（强制 cc），最后默认 claude。

**注意**：保留现有 `runtime=cc/api` 暗号（用户文本里说「换 api」），但映射改为 family（`api` → 默认 claude，`cc` → claude）。或者干脆废弃旧暗号。和 Zephyr 确认。

## 3. Provider 抓取 endpoint

新增 `GET /api/catalog/refresh?provider=anthropic|aistudio` —— 后端去对应供应商抓 model list，写到 `_CONFIG.home_path / ".catalog_cache.json"`。然后 `GET /api/catalog/list` 读缓存返回给前端。

- **Anthropic**: `GET https://api.anthropic.com/v1/models` with `x-api-key: $ANTHROPIC_API_KEY` header. 需要 `.env` 里有 `ANTHROPIC_API_KEY`（目前可能没有，待 Zephyr 确认）。
- **Google AI Studio**: `GET https://generativelanguage.googleapis.com/v1beta/models?key=$GOOGLE_AI_STUDIO_KEY`。
- **POE**: 没有公开 model list endpoint。维护硬编码白名单 `POE_KNOWN_MODELS = ["Claude-Opus-4.6", "Claude-Sonnet-4.6", ...]`，refresh 时返回该列表。
- **Vertex**: 类似 AIStudio 但走 GCP。先不做。

写在 `scripts/dashboard_server.py`，加 helper `_fetch_anthropic_models()` / `_fetch_aistudio_models()`，挂到 `_handle_api`。鉴权用现有 `_viewer_token_ok`（refresh 是写操作，加到 do_POST 那边，用 ingest token）。

## 4. Console UI

`dashboard/src/routes/+page.svelte`（overview）新增「Catalog」卡片：
- 当前每个 family 的 provider + model（只读显示）
- 「编辑」按钮 → 弹小窗 → 4 个下拉（family / provider / model / fallback chain）+ refresh 按钮
- 保存调 `POST /api/config/catalog` （后端写 fiam.toml 的对应段）

参考现有 plugins 页或 events 页 svelte 写法。Tailwind class 体系看 `+page.svelte` / `flow/+page.svelte`。

## 5. 测试

- 加 `tests/test_catalog.py`：parse fiam.toml catalog 段、route marker 解析、fallback 链选择
- 不要碰 `tests/test_app_runtime_router.py`（它有个 pre-existing fail，Copilot 会另处理）

## 6. 不要碰

- `src/fiam/runtime/turns.py` 的 `assistant_text_beats`（T1 在改）
- `src/fiam/runtime/prompt.py` 的 `build_api_messages`（T3 在改）
- `scripts/dashboard_server.py` 的 `_record_debug_context` / `_run_*_favilla_chat` 主体（T1 + 已有 debug 逻辑）
- `dashboard/src/routes/context/`（已存在，T1 可能扩展）

## 7. Commit 风格

- 小提交，每个步骤一个 commit
- Push main，告诉 Zephyr 我（Copilot）来 pull + restart + smoke
- Branch 名 `t2-catalog`（如果走 PR）或直接 main（看 Zephyr 偏好）

## 8. 可能的坑

- `fiam.toml` 写回时要保留注释和原顺序（用 `tomlkit` 而不是 `tomllib`），看 `scripts/fiam_lib/config_io.py` 是否已有 helper
- `extended_thinking` 在 POE 路径上未必透传，需要测
- AI 自主路由时容易陷死循环（每轮都 route 来 route 去），加个 cool-down（同一 family 至少 stick N 轮）
- gemini family 当前实际上是 fallback，没单独跑过完整 chat 流程，可能要补 runtime
