"""Unit tests for the /git/file range computation (roboco.api.routes.git).

Pure logic — no DB, no git. Covers the line/context windowing, explicit
range, whole-file cap, and truncation flag.
"""

from __future__ import annotations

from roboco.api.routes.git import _FILE_MAX_LINES, _compute_file_range


class TestComputeFileRange:
    def test_line_centers_context_window(self) -> None:
        s, e_, trunc = _compute_file_range(
            total=100, line=50, context=10, start=None, end=None
        )
        assert (s, e_, trunc) == (40, 60, True)

    def test_line_window_clamps_to_file_start(self) -> None:
        s, e_, trunc = _compute_file_range(
            total=100, line=3, context=10, start=None, end=None
        )
        assert (s, e_, trunc) == (1, 13, True)

    def test_line_window_clamps_to_file_end(self) -> None:
        s, e_, trunc = _compute_file_range(
            total=100, line=98, context=10, start=None, end=None
        )
        assert (s, e_, trunc) == (88, 100, False)

    def test_explicit_start_end_override_line(self) -> None:
        s, e_, trunc = _compute_file_range(
            total=100, line=50, context=10, start=5, end=8
        )
        assert (s, e_, trunc) == (5, 8, True)

    def test_whole_file_when_no_range_args(self) -> None:
        s, e_, trunc = _compute_file_range(
            total=50, line=None, context=10, start=None, end=None
        )
        assert (s, e_, trunc) == (1, 50, False)

    def test_whole_file_capped_when_huge(self) -> None:
        total = _FILE_MAX_LINES + 500
        s, e_, trunc = _compute_file_range(
            total=total, line=None, context=10, start=None, end=None
        )
        assert (s, e_, trunc) == (1, _FILE_MAX_LINES, True)

    def test_empty_file(self) -> None:
        s, e_, trunc = _compute_file_range(
            total=0, line=None, context=10, start=None, end=None
        )
        assert (s, e_, trunc) == (1, 1, False)

    def test_near_whole_file_explicit_range_still_capped(self) -> None:
        # start=1, end=total-1 is not the exact-whole-file shape, but the
        # resolved window is still oversized and must be capped.
        total = 50000
        s, e_, trunc = _compute_file_range(
            total=total, line=None, context=10, start=1, end=total - 1
        )
        assert (s, e_, trunc) == (1, _FILE_MAX_LINES, True)

    def test_oversized_line_context_window_is_capped(self) -> None:
        total = 10000
        s, e_, trunc = _compute_file_range(
            total=total, line=5000, context=3000, start=None, end=None
        )
        assert (s, e_, trunc) == (2000, 2000 + _FILE_MAX_LINES - 1, True)
