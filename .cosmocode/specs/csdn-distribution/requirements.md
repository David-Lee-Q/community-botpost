# Requirements Document

## Introduction

在现有卡奥斯开源社区发文链路之外，新增 CSDN 分发通道。分发通道以独立进程运行，消费原渠道已发布成功的文章，自动发布到 CSDN，并保证原渠道发布任务不受影响。

## Glossary

- **原渠道**：openlab.cosmoplat.com 发文链路（`bot/publish.py`、`bot/runner.py`、`bot/plan.json`）。
- **分发通道**：新增的 CSDN 发布链路。
- **分发任务**：一条待发布到 CSDN 的文章记录。
- **登录态**：CSDN 的 cookie 与本地存储快照，保存于 `channels/csdn/storage_state.json`。
- **人工验证码**：由用户在聊天中读取截图并回填的验证码或风控校验结果。
- **已发布文章**：`bot/plan.json` 中 `status` 等于 `published` 且具有 `article_id` 的条目。

## Requirements

### Requirement 1

**User Story:** AS 运营人员, I want 分发通道与原渠道完全隔离, so that CSDN 的故障不会影响原有发文任务。

#### Acceptance Criteria

1. 分发通道 SHALL 以只读方式读取 `bot/plan.json`。
2. 分发通道 SHALL 将运行状态写入 `channels/csdn/state.json`。
3. IF 分发通道执行失败，原渠道 SHALL 保持文章的已发布状态与已有记录不变。
4. 分发通道 SHALL 使用独立进程锁 `channels/csdn/.lock`。
5. 分发通道 SHALL 将日志写入 `channels/csdn/logs/csdn.log`。

### Requirement 2

**User Story:** AS 运营人员, I want 已发布文章自动进入 CSDN 分发队列, so that 日常发文无需额外操作。

#### Acceptance Criteria

1. WHEN `bot/plan.json` 出现 `status` 为 `published` 且发布时间在最近 7 天内的文章，分发通道 SHALL 在 60 秒内创建分发任务。
2. IF `channels/csdn/state.json` 已存在同一 `article_id` 的成功记录，分发通道 SHALL 跳过该文章。
3. 分发通道 SHALL 按文章发布时间从旧到新处理分发任务。
4. WHEN 用户指定回填模式，分发通道 SHALL 仅处理最近 7 天内的已发布文章。

### Requirement 3

**User Story:** AS 运营人员, I want 首次登录由人工完成验证码, so that 登录过程符合平台风控要求。

#### Acceptance Criteria

1. IF 登录态缺失或失效，分发通道 SHALL 暂停发布并在 `state.json` 标记 `needs_login`。
2. WHEN 用户运行登录助手，分发通道 SHALL 打开 CSDN 登录页并保存包含验证码的登录截图。
3. WHEN 用户回填验证码，登录助手 SHALL 使用该验证码完成登录并将登录态保存到 `storage_state.json`。
4. 登录助手 SHALL 以 `0600` 权限保存 `credentials.json` 与 `storage_state.json`。
5. 登录助手 SHALL 将手机号与密码从本地凭据文件读取，并避免写入日志。

### Requirement 4

**User Story:** AS 运营人员, I want 文章自动发布到 CSDN, so that 内容可以覆盖新增渠道。

#### Acceptance Criteria

1. WHEN 分发任务执行，分发通道 SHALL 在 CSDN Markdown 编辑器创建文章并设置标题、正文、封面、标签与专栏。
2. 分发通道 SHALL 将正文中的本地图片上传到 CSDN 并替换为 CSDN 图片地址。
3. 分发通道 SHALL 在提交后 30 秒内校验文章可访问并记录文章 URL。
4. IF 单篇分发失败，分发通道 SHALL 最多重试 3 次，重试间隔依次为 5 分钟、15 分钟、60 分钟。
5. 分发通道 SHALL 限制发布频率为每 3 分钟最多 1 篇、每天最多 20 篇。

### Requirement 5

**User Story:** AS 运营人员, I want 及时知道分发异常, so that 问题可以在当天被处理。

#### Acceptance Criteria

1. 分发通道 SHALL 将每次分发结果写入 `channels/csdn/logs/csdn.log`。
2. IF 连续 3 次分发失败，分发通道 SHALL 生成告警记录供日报读取。
3. IF 登录态失效，分发通道 SHALL 生成需要人工登录的告警记录。
4. 分发通道 SHALL 在 `state.json` 中统计待处理、成功、失败的数量。

### Requirement 6

**User Story:** AS 运营人员, I want 先看清待分发清单, so that 回填过程可控。

#### Acceptance Criteria

1. WHEN 用户以 `--dry-run` 运行分发通道，分发通道 SHALL 打印待分发文章清单且不执行发布。
2. WHEN 用户以回填模式运行分发通道，分发通道 SHALL 输出本次计划处理的总数与时间范围。
3. 分发通道 SHALL 在发布前校验标题非空、正文长度大于 200 字、封面文件存在。

### Requirement 7

**User Story:** AS 运营人员, I want CSDN 内容与主站一致, so that 两个渠道的品牌表达统一。

#### Acceptance Criteria

1. 分发通道 SHALL 复用原渠道文章的标题、摘要与正文。
2. IF 正文包含内部 OSS 图片地址，分发通道 SHALL 下载该图片并上传到 CSDN 后替换地址。
3. 分发通道 SHALL 在发布前执行敏感词与竞品词检查，命中时跳过该文章并记录原因。
