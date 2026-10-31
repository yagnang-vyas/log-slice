import pytest

from logslice.errors import UsageError
from logslice.parsers import ParseError, canonical_format, get_parser
from logslice.parsers.jsonl import JsonlParser
from logslice.parsers.nginx import NginxParser

COMBINED = (
    b'203.0.113.5 - alice [10/Oct/2026:13:55:36 +0000] "GET /api/users/42?x=1&y=2 HTTP/1.1" '
    b'200 512 "https://example.com/" "Mozilla/5.0 (X11)"'
)


class TestJsonl:
    parser = JsonlParser()

    def test_parses_object(self) -> None:
        assert self.parser.parse(b'{"a": 1, "b": {"c": [1, 2]}}') == {"a": 1, "b": {"c": [1, 2]}}

    def test_unicode(self) -> None:
        assert self.parser.parse('{"m": "héllo ✓"}'.encode()) == {"m": "héllo ✓"}

    def test_invalid_utf8_is_replaced_not_fatal(self) -> None:
        assert self.parser.parse(b'{"m": "a\xffb"}') == {"m": "a�b"}

    @pytest.mark.parametrize("line", [b"not json", b"[1, 2]", b'"str"', b"42", b'{"a":', b"null"])
    def test_rejects(self, line: bytes) -> None:
        with pytest.raises(ParseError):
            self.parser.parse(line)

    def test_deep_nesting_does_not_crash(self) -> None:
        with pytest.raises(ParseError):
            self.parser.parse(b"[" * 100_000)


class TestNginx:
    parser = NginxParser()

    def test_combined(self) -> None:
        rec = self.parser.parse(COMBINED)
        assert rec["remote_addr"] == "203.0.113.5"
        assert rec["remote_user"] == "alice"
        assert rec["time_local"] == "10/Oct/2026:13:55:36 +0000"
        assert (rec["method"], rec["path"], rec["query"], rec["protocol"]) == (
            "GET",
            "/api/users/42",
            "x=1&y=2",
            "HTTP/1.1",
        )
        assert rec["status"] == 200
        assert rec["body_bytes_sent"] == 512
        assert rec["http_referer"] == "https://example.com/"
        assert rec["http_user_agent"] == "Mozilla/5.0 (X11)"

    def test_dashes_become_null(self) -> None:
        line = b'1.2.3.4 - - [10/Oct/2026:13:55:36 +0000] "GET / HTTP/1.1" 304 - "-" "-"'
        rec = self.parser.parse(line)
        assert rec["remote_user"] is None
        assert rec["body_bytes_sent"] is None
        assert rec["http_referer"] is None
        assert rec["http_user_agent"] is None
        assert rec["query"] is None

    def test_common_format_without_referer_and_agent(self) -> None:
        line = b'1.2.3.4 - - [10/Oct/2026:13:55:36 +0000] "GET /a HTTP/1.0" 200 10'
        rec = self.parser.parse(line)
        assert rec["path"] == "/a"
        assert rec["http_user_agent"] is None

    def test_request_time_trailing_number(self) -> None:
        rec = self.parser.parse(COMBINED + b" 0.123")
        assert rec["request_time"] == 0.123

    def test_key_value_extras(self) -> None:
        rec = self.parser.parse(COMBINED + b" rt=0.250 upstream-status=502 cache=-")
        assert rec["rt"] == 0.25
        assert rec["upstream_status"] == 502
        assert rec["cache"] is None

    def test_malformed_request_line_keeps_row(self) -> None:
        line = b'1.2.3.4 - - [10/Oct/2026:13:55:36 +0000] "\\x16\\x03\\x01" 400 0 "-" "-"'
        rec = self.parser.parse(line)
        assert rec["status"] == 400
        assert rec["method"] is None and rec["path"] is None
        assert rec["request"] == "\\x16\\x03\\x01"

    def test_escaped_quotes_in_user_agent(self) -> None:
        line = (
            b'1.2.3.4 - - [10/Oct/2026:13:55:36 +0000] "GET / HTTP/1.1" 200 1 "-" '
            b'"a \\"quoted\\" ua"'
        )
        assert self.parser.parse(line)["http_user_agent"] == 'a \\"quoted\\" ua'

    def test_ipv6_and_hostname(self) -> None:
        line = b'2001:db8::1 - - [10/Oct/2026:13:55:36 +0000] "GET / HTTP/1.1" 200 1 "-" "-"'
        assert self.parser.parse(line)["remote_addr"] == "2001:db8::1"

    @pytest.mark.parametrize(
        "line",
        [b"", b"hello world", b'{"json": true}', b'1.2.3.4 - - [bad] "GET / HTTP/1.1" 200'],
    )
    def test_rejects(self, line: bytes) -> None:
        with pytest.raises(ParseError):
            self.parser.parse(line)


def test_registry_aliases() -> None:
    assert canonical_format("apache") == "nginx"
    assert canonical_format("ndjson") == "jsonl"
    assert get_parser("json").name == "jsonl"
    with pytest.raises(UsageError):
        get_parser("xml")
