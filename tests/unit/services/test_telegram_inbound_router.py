"""Pure-function coverage for telegram_inbound: command parsing, callback
build/parse round-trip, and chat-id authorization. No I/O, no DB."""

from __future__ import annotations

import pytest
from roboco.config import settings as cfg
from roboco.services.telegram_inbound import (
    _MESSAGE_CHAR_LIMIT,
    ParsedCallback,
    _authorized_chat,
    _esc,
    _esc_attr,
    _truncate,
    build_action_keyboard,
    build_callback,
    parse_callback,
    parse_command,
    render_queue_item_text,
)

_CALLBACK_DATA_MAX_BYTES = 64  # mirrors Telegram's own callback_data cap
_KEYBOARD_ROW_LEN_NO_OPEN_BUTTON = 2  # Approve + Reject, no panel_base_url


class TestParseCommand:
    def test_plain_command(self) -> None:
        assert parse_command("/status") == ("status", "")

    def test_command_with_args(self) -> None:
        assert parse_command("/task abc12345 extra words") == (
            "task",
            "abc12345 extra words",
        )

    def test_strips_botname_suffix(self) -> None:
        assert parse_command("/status@my_roboco_bot") == ("status", "")

    def test_non_command_text_is_empty(self) -> None:
        assert parse_command("hello there") == ("", "")

    def test_empty_text_is_empty(self) -> None:
        assert parse_command("") == ("", "")

    def test_case_insensitive(self) -> None:
        assert parse_command("/STATUS") == ("status", "")


class TestCallbackRoundTrip:
    @pytest.mark.parametrize(
        "action,kind,id8,extra",
        [
            ("apv", "task", "a1b2c3d4", ""),
            ("rej", "release", "deadbeef", ""),
            ("apv", "xpost", "12345678", ""),
            ("rej", "video", "87654321", ""),
            ("apv", "roadmap", "abcdef12", "item-3"),
        ],
    )
    def test_build_then_parse_round_trips(
        self, action: str, kind: str, id8: str, extra: str
    ) -> None:
        data = build_callback(action, kind, id8, extra)
        parsed = parse_callback(data)
        assert parsed == ParsedCallback(action=action, kind=kind, id8=id8, extra=extra)

    def test_callback_data_stays_under_64_bytes(self) -> None:
        data = build_callback("apv", "roadmap", "a1b2c3d4", "item-99")
        assert len(data.encode()) <= _CALLBACK_DATA_MAX_BYTES

    def test_oversized_callback_raises(self) -> None:
        with pytest.raises(ValueError, match="64 bytes"):
            build_callback("apv", "roadmap", "a1b2c3d4", "x" * 60)

    def test_parse_rejects_unknown_action(self) -> None:
        assert parse_callback("nope:task:a1b2c3d4") is None

    def test_parse_rejects_unknown_kind(self) -> None:
        assert parse_callback("apv:bogus:a1b2c3d4") is None

    def test_parse_rejects_malformed_shape(self) -> None:
        assert parse_callback("apv:task") is None
        assert parse_callback("apv:task:a1:b2:c3") is None

    def test_parse_rejects_empty_string(self) -> None:
        assert parse_callback("") is None

    def test_parse_rejects_empty_id8(self) -> None:
        assert parse_callback("apv:task:") is None


class TestActionKeyboard:
    def test_builds_approve_reject_row(self) -> None:
        kb = build_action_keyboard("task", "a1b2c3d4")
        row = kb["inline_keyboard"][0]
        assert row[0] == {
            "text": "Approve",
            "callback_data": "apv:task:a1b2c3d4",
        }
        assert row[1] == {
            "text": "Reject",
            "callback_data": "rej:task:a1b2c3d4",
        }

    def test_omits_open_button_without_panel_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cfg, "panel_base_url", "")
        kb = build_action_keyboard("task", "a1b2c3d4")
        assert len(kb["inline_keyboard"][0]) == _KEYBOARD_ROW_LEN_NO_OPEN_BUTTON

    def test_includes_open_button_with_panel_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cfg, "panel_base_url", "https://panel.example.com")
        kb = build_action_keyboard("task", "a1b2c3d4")
        row = kb["inline_keyboard"][0]
        assert row[2] == {
            "text": "Open",
            "url": "https://panel.example.com/tasks/a1b2c3d4",
        }

    def test_roadmap_deep_link_carries_no_item_id_it_points_at_the_cycle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cfg, "panel_base_url", "https://panel.example.com")
        kb = build_action_keyboard("roadmap", "a1b2c3d4", "item-2")
        row = kb["inline_keyboard"][0]
        assert row[2]["url"] == "https://panel.example.com/overview"
        assert row[0]["callback_data"] == "apv:roadmap:a1b2c3d4:item-2"


class TestAuthorizedChat:
    def test_matching_chat_id_authorized(self) -> None:
        assert _authorized_chat("12345", "12345") is True

    def test_mismatched_chat_id_rejected(self) -> None:
        assert _authorized_chat("99999", "12345") is False

    def test_empty_chat_id_rejected(self) -> None:
        assert _authorized_chat("", "12345") is False


class TestEsc:
    def test_escapes_angle_brackets_and_ampersand(self) -> None:
        assert _esc("<b>bold&joke</b>") == "&lt;b&gt;bold&amp;joke&lt;/b&gt;"

    def test_quotes_are_left_alone(self) -> None:
        # quote=False — _esc renders HTML text nodes, where quotes need no
        # escaping. Attribute values (e.g. href="...") go through _esc_attr
        # instead, which does escape them.
        assert _esc('it\'s "fine"') == 'it\'s "fine"'

    def test_stringifies_non_str_values(self) -> None:
        assert _esc(42) == "42"


class TestEscAttr:
    def test_escapes_quotes_and_angle_brackets(self) -> None:
        assert _esc_attr("""a"b'c<d>e""") == "a&quot;b&#x27;c&lt;d&gt;e"

    def test_stringifies_non_str_values(self) -> None:
        assert _esc_attr(42) == "42"


class TestTruncateHtml:
    def test_short_text_is_untouched(self) -> None:
        assert _truncate("hello") == "hello"

    def test_backs_off_before_an_unclosed_angle_bracket(self) -> None:
        # A naive slice at `limit - 1` would land inside "<code>", leaving a
        # bare '<' Telegram's HTML parser can't make sense of — back off to
        # before it instead.
        text = ("x" * 4093) + "<code>"
        result = _truncate(text)
        assert result.endswith("…")
        assert not result.rstrip("…").endswith("<")

    def test_backs_off_before_an_unclosed_entity(self) -> None:
        text = ("x" * 4093) + "&amp;"
        result = _truncate(text)
        assert result.endswith("…")
        assert "&am" not in result

    @pytest.mark.parametrize("title_len", range(4048, 4069))
    def test_render_queue_item_truncation_balances_code_tag(
        self, title_len: int
    ) -> None:
        # Regression: a naive char-count slice landed inside the trailing
        # `<code>id8</code>` span, shipping an unclosed `<code>` Telegram's
        # HTML parser rejects outright.
        text = render_queue_item_text("roadmap", "abc12345", "item-0", "A" * title_len)
        assert text.count("<code>") == text.count("</code>")
        assert len(text) <= _MESSAGE_CHAR_LIMIT

    def test_truncate_balances_a_bold_wrapped_tag(self) -> None:
        text = "<b>" + ("y" * 4200) + "</b>"
        result = _truncate(text)
        assert result.count("<b>") == result.count("</b>")
        assert len(result) <= _MESSAGE_CHAR_LIMIT


class TestRenderQueueItemText:
    def test_escapes_html_in_title(self) -> None:
        text = render_queue_item_text("task", "a1b2c3d4", "", "<b>bold&joke</b>")
        assert "&lt;b&gt;bold&amp;joke&lt;/b&gt;" in text
        assert "<b>bold&joke</b>" not in text

    def test_kind_emoji_and_label_per_kind(self) -> None:
        assert render_queue_item_text("release", "a1b2c3d4", "", "x").startswith(
            "🚀 <b>Release</b>"
        )
        assert render_queue_item_text("video", "a1b2c3d4", "", "x").startswith(
            "🎬 <b>Video</b>"
        )
        assert render_queue_item_text("xpost", "a1b2c3d4", "", "x").startswith(
            "✕ <b>Post</b>"
        )
        assert render_queue_item_text("roadmap", "a1b2c3d4", "", "x").startswith(
            "🗺️ <b>Roadmap</b>"
        )
        assert render_queue_item_text("task", "a1b2c3d4", "", "x").startswith(
            "📋 <b>Task</b>"
        )

    def test_id8_and_extra_render_as_code_span(self) -> None:
        text = render_queue_item_text("roadmap", "a1b2c3d4", "item-2", "x")
        assert "<code>a1b2c3d4:item-2</code>" in text

    def test_no_extra_omits_suffix(self) -> None:
        text = render_queue_item_text("task", "a1b2c3d4", "", "x")
        assert "<code>a1b2c3d4</code>" in text
