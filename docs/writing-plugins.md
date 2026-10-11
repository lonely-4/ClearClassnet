# 编写插件

插件在导入时注册 matcher。框架启动时会加载：

- 内置参考插件：`audit`、`blocklist`、`mitm_policy`（可用 `plugins.disabled` 按短名关掉）
- `[plugins].dirs` 里的 `*.py`
- entry point 组 `clearclassnet.plugins`

加载失败会记入日志并跳过该插件，不会让进程退出。

## 请求

```python
from clearclassnet import RequestEvent, Rule, on_request

@on_request(Rule.host(["douyin.com", "*.douyin.com", "*.tiktok.com"]), priority=10)
async def block_video(event: RequestEvent) -> None:
    event.reject(status=403, body=b"Blocked in class")

@on_request(Rule.method("POST") & Rule.path("/api/*"), block=False)
async def rewrite_body(event: RequestEvent) -> None:
    data = await event.json()
    data["school"] = "demo"
    event.rewrite(json=data, headers={"X-Tag": "classnet"})
    event.headers["X-Foo"] = "bar"
```

`headers` 是大小写不敏感的可变映射，直接赋值就会体现在转发的请求上。`rewrite()` 还可以改 `method`、`url`、`path`、`body`、`text`。

`respond(status, headers, body)` 直接把响应交给客户端，不再连接上游。`reject()` 是它的拦截写法。`allow()` 按当前内容转发，并停止后续 matcher。

## 响应

```python
from clearclassnet import ResponseEvent, Rule, on_response

@on_response(Rule.host("*.example.com"))
async def inject(event: ResponseEvent) -> None:
    if "text/html" in (event.headers.get("Content-Type") or ""):
        event.rewrite(body=(await event.text()).replace("</body>", "<p>hi</p></body>"))
```

## CONNECT

```python
from clearclassnet import ConnectEvent, Rule, on_connect

@on_connect(Rule.host("*.bank.com"))
async def leave_alone(event: ConnectEvent) -> None:
    event.tunnel()
```

`intercept()` 解密并继续产生请求和响应事件。`tunnel()` 盲转发。`reject()` 拒绝连接。

## 优先级

数字更大的 matcher 先运行。需要记录“最终被拒绝的请求”时，把观察逻辑放在更高优先级并设置 `block=False`。内置 `audit` 使用 `priority=1000` 就是这个原因。

## 配置

核心只读取 `[proxy]` 和 `[logging]`。插件用 `plugin_config("名字")` 读取 `[plugins.名字]`：

```python
from clearclassnet import plugin_config

path = plugin_config("blocklist").get("path", "")
```

## 自定义规则

```python
from clearclassnet import Rule

Rule.custom(lambda event: event.path.startswith("/admin"), "admin")
```

内置规则有 `Rule.all`、`host`、`method`、`path`、`scheme`、`port`。`host` 和 `path` 使用 `fnmatch`，`*.example.com` 不匹配 `example.com` 本身。`Rule.host` 也可以直接传入一组模式，命中其中任意一个即可。
