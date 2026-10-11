from fnmatch import fnmatch

from clearclassnet.dispatch.rule import Rule
from tests.helpers import request_event


def test_host_method_path_and_operators() -> None:
    event = request_event(method="POST", host="api.example.com", path="/v1/items", port=80)
    rule = Rule.method("post") & Rule.host("*.example.com") & Rule.path("/v1/*")
    assert rule.matches(event)
    assert not (~Rule.method("POST")).matches(event)
    assert (Rule.host("other.test") | Rule.port(80)).matches(event)
    assert Rule.scheme("http").matches(event)
    assert not Rule.scheme("https").matches(event)


def test_host_accepts_several_patterns() -> None:
    rule = Rule.host(["douyin.com", "*.douyin.com"])
    assert rule.matches(request_event(host="douyin.com"))
    assert rule.matches(request_event(host="www.douyin.com"))
    assert not rule.matches(request_event(host="douyinstatic.com"))
    assert repr(rule) == "Rule.host(['douyin.com', '*.douyin.com'])"


def test_host_pattern_does_not_match_parent() -> None:
    event = request_event(host="example.com")
    assert not Rule.host("*.example.com").matches(event)
    assert Rule.host("example.com").matches(event)


def test_custom_rule() -> None:
    event = request_event(path="/secret")
    rule = Rule.custom(lambda item: item.path == "/secret", "secret")
    assert rule.matches(event)
    assert repr(rule) == "secret"


def test_fnmatch_is_what_host_rules_use() -> None:
    assert fnmatch("www.douyin.com", "*.douyin.com")


def test_url_query_get_decodes_the_first_value() -> None:
    event = request_event(path="/search", query="keyword=%E6%B5%8B%E8%AF%95&page=2")
    assert event.url.query.get("keyword", "") == "测试"
    assert event.url.query.get("missing", "") == ""
    assert str(event.url).endswith("/search?keyword=%E6%B5%8B%E8%AF%95&page=2")
    assert event.url.casefold() == str(event.url).casefold()
