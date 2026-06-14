# Markdown Channel Bot

把私聊发送给 bot 的 Markdown 内容转成 Telegram Bot API `sendRichMessage` 预览。确认后点击按钮，bot 会把同一份 Rich Message 发送到配置的频道；取消则丢弃草稿。

## 功能

- 只允许 `TELEGRAM_ALLOWED_USER_IDS` 中的用户使用。
- 支持直接发送 Markdown 文本。
- 支持上传 `.md`、`.markdown`、`.txt` 或 `text/*` 文件。
- 使用 Telegram Bot API `sendRichMessage`，请求体中的 `rich_message.markdown` 会原样使用你的 Markdown。
- 预览消息下方提供 `发送到频道` 和 `取消` 两个 inline keyboard 按钮。
- 支持用 `/edit` 编辑已经由 bot 发到频道的 Rich Message，更新时调用 Telegram Bot API `editMessageText` 的 `rich_message` 参数。
- 启动时自动调用 Telegram Bot API `setMyCommands` 注册命令菜单，让客户端展示 `/edit`。
- 待确认草稿会持久化到 `/data/pending.json`，容器重启后仍可继续处理未过期草稿。

## 配置

复制配置模板：

```bash
cp .env.example .env
```

编辑 `.env`：

```dotenv
TELEGRAM_BOT_TOKEN=123456:replace-with-your-token
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
TELEGRAM_CHANNEL_ID=-1001234567890
```

说明：

- `TELEGRAM_BOT_TOKEN`：从 BotFather 获取。
- `TELEGRAM_ALLOWED_USER_IDS`：允许使用 bot 的 Telegram 用户 ID，逗号分隔。
- `TELEGRAM_CHANNEL_ID`：目标频道 ID，例如 `-1001234567890`，也可以是公开频道用户名如 `@your_channel`。如果你拿到的是频道内部 ID `1234567890`，通常需要写成 `-1001234567890`。
- `PENDING_TTL_SECONDS`：草稿有效期，默认 86400 秒。
- `MAX_DOCUMENT_BYTES`：上传文件大小上限，默认 256 KiB。
- `MAX_RICH_MESSAGE_CHARS`：Rich Message 字符数上限，默认 32768。

目标频道需要把 bot 加为管理员，并授予发消息权限。


## Docker 部署

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f md-channel-bot
```

停止：

```bash
docker compose down
```


## 使用

### 命令菜单

bot 启动时会自动注册这些命令：

```text
/start - 开始使用 bot
/help - 查看使用说明
/edit - 编辑频道消息，参数可用 URL 或消息 ID
```

### 发布新消息

1. 用白名单用户私聊 bot。
2. 直接发送 Markdown 文本，或上传 `.md` 文件。
3. bot 返回 Rich Message 预览。
4. 点击 `发送到频道` 发布到 `TELEGRAM_CHANNEL_ID`，或点击 `取消` 丢弃。

### 编辑频道消息

消息必须是 bot 有权限访问并且可编辑的频道消息；通常也需要是这个 bot 发送的消息。

支持两种写法：

```text
/edit 123
/edit https://t.me/c/1326206584/123
```

也支持公开频道链接：

```text
/edit https://t.me/your_channel/123
```

流程：

1. bot 复制这条频道消息到私聊，并在下方显示 `编辑` 和 `取消`。
2. 点击 `编辑` 后，bot 提示 `请发送新内容来替换消息`。
3. 发送新的 Markdown 文本或 `.md` 文件。
4. bot 返回 Rich Message 预览。
5. 点击 `更新到频道` 后，bot 使用 `editMessageText` 更新原频道消息；点击 `取消` 则丢弃本次编辑草稿。

## 本地运行

```bash
export PYTHONPATH=src
export TELEGRAM_BOT_TOKEN=123456:replace-with-your-token
export TELEGRAM_ALLOWED_USER_IDS=123456789
export TELEGRAM_CHANNEL_ID=-1001234567890
python -m md_channel_bot
```

## 测试

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

## API 参考

实现使用的是 Telegram Bot API 的 long polling、`sendRichMessage`、`editMessageText`、`setMyCommands`、inline keyboard callback 和 `answerCallbackQuery`。
