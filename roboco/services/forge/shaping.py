"""Response shaping shared by non-GitHub transports.

Adapters translate a forge's native wire contract back into the GitHub
shapes ``GitService`` classifies; :class:`ShapedResponse` is the stand-in
they return. Callers only ever read ``status_code`` / ``is_success`` /
``text`` / ``json()`` (the seam's documented contract), so that is the
whole surface.
"""

from __future__ import annotations

from typing import Any

import httpx


class ShapedResponse:
    """An httpx.Response stand-in with translated status/body/text.

    Wraps the real response for fidelity while letting the adapter override
    the JSON payload, the status code, and/or the text (GitLab's diff
    reassembly returns synthesized unified-diff text).
    """

    def __init__(
        self,
        real: httpx.Response,
        *,
        json_payload: Any | None = None,
        status_code: int | None = None,
        text: str | None = None,
    ) -> None:
        self._real = real
        self._json = json_payload
        self._text = text
        self.status_code = status_code if status_code is not None else real.status_code

    @property
    def is_success(self) -> bool:
        return httpx.codes.is_success(self.status_code)

    @property
    def text(self) -> str:
        if self._text is not None:
            return self._text
        return self._real.text

    def json(self) -> Any:
        if self._json is not None:
            return self._json
        return self._real.json()
