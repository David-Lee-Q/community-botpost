# 多平台一键分发桥（CSDN同步助手 MCP 协议）

对接用户本机 Chrome 上已安装的「CSDN同步助手」扩展（v1.2.1）。扩展是 WebSocket 客户端，
本桥是 WebSocket 服务端，负责：

1. **登录态监测**：周期性调用 `listPlatforms`，记录各平台登录态到 `state.json`，本地看板展示
2. **一键同步**：`syncArticle` 把 markdown 文章同步到已登录平台，**产出草稿**（MCP 只支持草稿，
   同步后需去各平台后台点发布）
3. **本地图片上传**：markdown 里的本地图片路径自动走 `uploadImage` 上传后替换（单图 ≤5MB）

> 本目录必须在**用户本机**（装了扩展的 Chrome 所在机器）运行，workspace 服务器无法执行。

## 安装

```bash
pip3 install websockets
```

## 使用步骤

1. 配置 Token（必须一致）：扩展启用 MCP 时会自动生成 Token（弹窗「MCP 配置」里可见），
   桥端必须用同一个值，否则扩展对每个请求都回 403。把弹窗里的 Token 填入
   `config.json`（与 `mcp_bridge.py` 同目录，参考 `config.example.json`）：

   ```json
   { "token": "弹窗里显示的 Token" }
   ```

2. 启动常驻桥（WS 服务 + 本地看板 + 登录态自动监测）：

   ```bash
   python mcp_bridge.py serve
   ```

   输出 `WS 服务: ws://127.0.0.1:3721  看板: http://127.0.0.1:8390`

3. 打开扩展弹窗（Chrome 工具栏「CSDN同步助手」）：
   - 打开「启用 MCP / WebSocket」开关
   - Server 地址保持默认 `ws://127.0.0.1:3721`（非本机地址必须 wss://）
   - 若刚改过 Token：把 MCP 开关关一次再打开（或最多等 60 秒自动重连）
   - 连接成功后看板左上角变为「扩展已连接」

4. 在各平台完成登录（扩展用浏览器 Cookie 判断登录态，看板每 10 分钟自动刷新，
   也可点「刷新登录态」）

5. 同步文章，三种方式任选：
   - 看板 http://127.0.0.1:8390 ：粘贴/载入 markdown → 勾选平台 → 同步
   - 每平台一个脚本：`python3 sync_juejin.py 文章.md`（共 17 个，见下方清单）
   - 全量同步到所有已登录平台：`python3 mcp_bridge.py sync-all 文章.md`

## 命令行

```bash
python3 mcp_bridge.py serve                # 常驻桥（看板+监测+WS）
python3 mcp_bridge.py platforms            # 打印各平台登录态
python3 mcp_bridge.py sync  --platform juejin 文章.md [--title T] [--cover U] [--tags a,b] [--retry]
python3 mcp_bridge.py sync-all 文章.md [--platforms juejin,weibo] [--title T] [--cover U] [--tags a,b]
python3 mcp_bridge.py sync-bot 文章目录 [--platforms a,b] [--latest N] [--tags a,b] [--delay 10]
python3 sync_<平台id>.py 文章.md            # 单平台薄封装，等价 sync --platform <平台id>
```

`sync`/`sync-all`/`sync-bot` 优先走运行中的常驻桥（HTTP 8390），未运行则自动起临时 WS 等扩展连接
（最多 `standalone_wait_sec` 秒）。

### 内容不用自己写

community-bot 每天生成的 4 篇 markdown（工作空间 `bot-articles/` 目录，标题在首行 `# 标题`）就是
现成内容，流程：

1. 从工作空间「项目文件」面板下载当日的 `.md` 文件（`bot-articles/` 下当天那几篇）
2. 批量同步到全部已登录平台：

   ```bash
   python mcp_bridge.py sync-bot .\今日文章 --latest 4 --tags 人工智能,工业软件
   ```

   - `--latest 4` 只同步文件名排序后最近 4 篇（每天正好 4 篇）
   - 标题自动取各文件首行；markdown 里引用的本地图片自动上传替换
   - 篇间隔默认 10 秒（`--delay` 可调），某篇失败会跳过并继续
   - 结果均为各平台**草稿**，去各平台后台点发布

## 平台清单（17 个，扩展支持 MCP 的全部平台）

| 脚本 | 平台 | 格式 | 标题限制 |
|------|------|------|---------|
| sync_zhihu.py | 知乎 | html | ≤100 字 |
| sync_juejin.py | 掘金 | markdown | - |
| sync_weibo.py | 微博 | html | ≤32 字 |
| sync_toutiao.py | 头条 | html | - |
| sync_baijiahao.py | 百家号 | html | ≤64 字 |
| sync_wechat.py | 微信公众号 | html | - |
| sync_cnblogs.py | 博客园 | markdown | - |
| sync_51cto.py | 51CTO | markdown | - |
| sync_jianshu.py | 简书 | html | - |
| sync_segmentfault.py | 思否 | markdown | - |
| sync_oschina.py | 开源中国 | markdown | - |
| sync_tencentcloud.py | 腾讯云开发者 | markdown | ≤80 字 |
| sync_aliyun.py | 阿里云开发者 | markdown | - |
| sync_netease.py | 网易号 | html | 仅草稿 |
| sync_bilibili.py | 哔哩哔哩 | html | - |
| sync_douyin.py | 抖音长文 | markdown | ≤30 字 |
| sync_xiaohongshu.py | 小红书长文 | markdown | ≤64 字 |

AI 图文变体（抖音图文/小红书图文）不支持 MCP，不在清单内。

## 前置条件与限制

- Chrome 保持打开，且有一个**普通网页标签页处于激活状态**（扩展靠它跑内容预处理脚本）
- 同步结果各平台均为**草稿**，去各平台后台点发布
- 标题超限的平台由扩展自动截断或报错，建议控制在限制内
- 监测间隔、超时等可调 `config.json`（参考 `config.example.json`）

## 协议要点（逆向自扩展 v1.2.1）

- 请求帧：`{"id": uuid, "token": ..., "method": ..., "params": {...}}`
- 响应帧：`{"id": ..., "result": ...}` 或 `{"id": ..., "error": {"code", "message"}}`
- keepalive：扩展发 `{"type":"ping"}`，本桥必须回 `{"type":"pong"}`（60s 无 pong 扩展重连）
- `syncArticle` 参数：`idempotencyKey`（必填 ≤128）、`platforms`（非空）、
  `article.{title,markdown|content,cover?,tags?}`；`draft:false` 会被扩展拒绝
- `uploadImage`：`{imageData: base64, mimeType, platform}` → `{url, platform}`，单图 ≤5MB
