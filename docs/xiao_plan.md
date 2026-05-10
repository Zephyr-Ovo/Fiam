# XIAO / Limen Plan

> 状态：**计划文档，未实施**。XIAO ESP32-S3 圆屏外设的下一阶段路径。

## 0. 当前状态

- 硬件：Seeed XIAO ESP32-S3 + Round Display + 摄像头模块
- 固件：`channels/limen/`（PlatformIO，Arduino framework）
- 通道：HTTP polling `https://fiet.cc/api/wearable/{message,reply}`，token 通过 `FIAM_INGEST_TOKEN`
- 内容形态：短消息 / 颜文字 / 语义 emoji（spark/heart/smile/moon/check/alert）
- 已知不稳定：直连 fiet.cc 经 Cloudflare TLS 在手机热点/移动网络下经常握手失败
- 摄像头：暂时禁用（功耗 + 隐私 + 不需要）

## 1. 网络方案（按推荐度）

### 路径 A：XIAO → 手机热点 → Favilla 中转
```
XIAO ──WiFi (phone hotspot)──► Favilla (LAN HTTP relay) ──► fiet.cc
```
- Favilla 起一个 NSD 广播的 HTTP server (8765)，XIAO 用 mDNS 发现
- 优点：TLS/DNS 由手机处理，XIAO 只跑明文 HTTP；电池续航好
- 缺点：XIAO 必须连同一热点；离开 Favilla 就哑

### 路径 B：Tailscale on XIAO
- 编译 `tailscale-c-go` 或用 `easytier`/`zerotier`
- XIAO 加入 tailnet → 直连 ISP fiet-den 内网 IP
- 优点：跨网络可达，端到端加密
- 缺点：固件重 + 内存紧张；ESP32-S3 8MB PSRAM 勉强够

### 路径 C：MQTT 到地理就近 broker
- 在腾讯云/阿里云 香港/新加坡 起一个 mosquitto，XIAO 连这里
- Server 端 bridge 到 ISP 的 mosquitto
- 优点：长连接 + 推送（无需 polling）；省电
- 缺点：多一跳基础设施；TLS 还是要做（或者纯 TCP + IP 白名单）

### 路径 D：保持当前 HTTP polling，只调整后端
- 继续 polling，但把 XIAO 端的 SSL 握手降级（只校验固定证书指纹）
- 中等改动，但治标不治本

**推荐：先做 A（Favilla 中转）+ 同步研究 B/C**

## 2. 协议形态

### 当前：HTTP polling
- 5s 一次 GET `/api/wearable/reply` → 显示 message/kaomoji/emoji
- POST `/api/wearable/message` → 上报按钮事件 / 传感器读数

### 升级：MQTT pub/sub
```
XIAO subscribe:
  fiam/xiao/screen     ← message / kaomoji / emoji
  fiam/xiao/control    ← brightness / sleep / refresh
XIAO publish:
  fiam/xiao/event      → button press / motion / battery
  fiam/xiao/sensor     → 环境光、温度（如果加传感器）
```

### 升级：WebSocket (轻量 push)
- ESP32-S3 跑 `WebSockets` 库稳定
- 单连接，server push 屏幕内容；XIAO push 事件
- 比 MQTT 简单，比 polling 实时

**建议优先级**：A 路径下用 mDNS + WebSocket（Favilla 起 ws server，XIAO 连 ws://favilla.local:8765/xiao）

## 3. 电源

- **当前**：USB-C dev power（开发期）
- **下一步**：40300 LiPo
  - 必须先验证：电池正负极（XIAO 的 BAT+/BAT- 引脚位置）、充电限流（默认 100mA OK）、过放保护
  - 圆屏 + WiFi 满载 ~150mA，40300（500mAh）理论 3h；实际开 deep-sleep 可拉到 12-24h
- 实现 deep-sleep + ESP32 RTC 唤醒：
  - 屏幕 OFF + WiFi OFF → ~10μA
  - 每 30s 醒来 polling 或等按钮中断

## 4. 屏幕内容协议（保持不变）

```
[→xiao:screen] message:今天的咖啡好喝
[→xiao:screen] kaomoji:(´• ω •`)
[→xiao:screen] emoji:spark
```

- message ≤ 80 chars（圆屏窄）
- kaomoji ASCII，避免半角全角混排
- emoji 用语义名，固件 fallback：
  - 优先：本地 24x24 单色 PNG
  - fallback：Unicode emoji 字符（U+1F525 等）
- 多帧动画：`[→xiao:screen] anim:pulse` → 固件查表

## 5. 摄像头（远期）

如果重新启用：
- **不传图给 Claude**（太贵 + 隐私）
- 本地 ESP32-CAM JPEG → Favilla（LAN）→ 视觉模型（用 Favilla Settings 里配置的 vision API）→ 描述文本入 flow
- 用例：扫桌面物品、捕捉表情瞬间、ambient 环境快照

## 6. 触控（未启用）

- 圆屏带触控（GT911）
- 启用后协议：
  ```
  XIAO publish:
    fiam/xiao/touch  → {x, y, gesture: tap|long|swipe}
  ```
- 用例：滑动查看消息历史、长按夹断当前 TTS

## 7. Stroll mode（外出）

XIAO 当 Claude 的"外化感官"：
- 屏幕 ambient 显示当前心情/状态
- 麦克风（如果加）→ ambient capture → STT → 给 Claude 一句"刚才在咖啡店听到 xxx"
- 喇叭（如果加）→ 私密 TTS（贴近耳朵）

**先做屏幕 + 按钮，麦/喇叭分开 PR。**

## 8. 不做（先记下）

- 触屏全 GUI（Limen 不该当手机用）
- 长视频流
- BLE peripheral（连手机）— WiFi 中转就够
- 自带电池仪表 IC（用 ADC 读电压估算就行）
