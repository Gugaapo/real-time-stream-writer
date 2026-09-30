"""DeepSeek describer that keeps the usage object from the streamed response."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Callable, Iterator

from streamkit.describe import DescribeError, OpenAICompatDescriber


class MeteredDescriber(OpenAICompatDescriber):
    """Same call as streamkit, plus ``stream_options.include_usage``."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.last_usage: dict | None = None

    def describe(self, *, system: str, user: str, image_png: bytes, on_token=None) -> str:
        self.last_usage = None
        return super().describe(system=system, user=user, image_png=image_png, on_token=on_token)

    def _request(
        self,
        *,
        system: str,
        user: str,
        image_png: bytes,
        api_key: str,
        on_token: Callable[[str], None] | None,
    ) -> str:
        b64 = base64.b64encode(image_png).decode("ascii")
        data_url = f"data:image/png;base64,{b64}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if "deepseek" in self.base_url.lower() or "deepseek" in self.model.lower():
            payload["thinking"] = {"type": "disabled"}
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint(),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return "".join(self._iter_sse(resp, on_token))
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise DescribeError(
                f"HTTP {exc.code}: {err_body[:400]}",
                status=int(exc.code),
                body=err_body,
            ) from None
        except urllib.error.URLError as exc:
            raise DescribeError(f"URL error: {exc.reason}", status=None, body=str(exc)) from None
        except TimeoutError as exc:
            raise DescribeError(f"timeout: {exc}", status=408, body=str(exc)) from None

    def _iter_sse(self, resp, on_token: Callable[[str], None] | None) -> Iterator[str]:
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            usage = obj.get("usage")
            if isinstance(usage, dict) and usage:
                self.last_usage = usage
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if not piece:
                msg = choices[0].get("message") or {}
                piece = msg.get("content")
            if piece:
                if on_token:
                    on_token(piece)
                yield piece
