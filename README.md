# ClearClassnet

本机 MITM 代理框架。核心只负责收发、TLS 和事件分发；拦截、改写、是否解密，都由插件决定。

把系统代理指到 `127.0.0.1:8899` 后，每个 HTTP 请求和响应都会变成一个事件，插件可以 `reject`、`allow`、`rewrite` 或 `respond`。

## 快速开始

```bash
uv sync --all-groups
uv run clearclassnet ca export --out clearclassnet-ca.pem
uv run clearclassnet ca install
uv run clearclassnet run --config presets/example/clearclassnet.toml
```

`ca install` 会把本地 CA 装进当前用户的信任库。未信任该 CA 的程序无法完成 HTTPS 解密，流量会在握手阶段失败。

`clearclassnet check` 打印已经注册的 matcher，用来确认插件是否加载。

## 写一个插件

```python
from clearclassnet.dispatch import Rule, on_request
from clearclassnet.event import RequestEvent

@on_request(Rule.host("*.example.com"), priority=10)
async def block(event: RequestEvent):
    event.reject(status=403, body=b"Blocked in class")
```

把文件放到配置里的 `plugins.dirs`，或通过 entry point 组 `clearclassnet.plugins` 发布。参考实现 `audit`、`blocklist`、`mitm_policy` 与第三方插件使用同一套 API，可以在配置里禁用。

规则用 `&`、`|`、`~` 组合。数字更大的 `priority` 先执行。`block=False` 的处理器只观察，不挡住后面的 matcher；`reject()`、`allow()`、`respond()` 总会停止后续匹配。

## 限制

- 只解析 HTTP/1.1。MITM 时 ALPN 固定为 `http/1.1`。
- WebSocket 在收到 `101` 之后改为字节转发，不再解析帧内容。
- 超过 `body_limit`（默认 1 MiB）的正文仍然会转发，但 `bytes()` / `text()` / `json()` 会失败，也不能改写正文。

更多说明见 [docs/architecture.md](docs/architecture.md) 和 [docs/writing-plugins.md](docs/writing-plugins.md)。
