"""
MCP client for the HubSpot MCP server.

Spawns (or reuses) the @hubspot/mcp-server subprocess and executes
tool calls over stdin/stdout using the MCP JSON-RPC protocol.

The server is started lazily on first use and kept alive for the
process lifetime.  All calls are serialised through an asyncio lock
so concurrent agent turns don't interleave JSON-RPC frames.

Usage::

    client = HubSpotMCPClient()
    result = await client.call("hubspot_upsert_contact", {"email": "x@y.com"})
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# MCP JSON-RPC protocol version
_JSONRPC_VERSION = "2.0"
_INIT_TIMEOUT = 30.0   # seconds to wait for server ready
_CALL_TIMEOUT = 30.0   # seconds per tool call


class HubSpotMCPClient:
    """Async client for the @hubspot/mcp-server subprocess.

    Manages the server lifecycle and serialises JSON-RPC calls.

    Args:
        access_token: HubSpot private-app token. Defaults to
            ``HUBSPOT_ACCESS_TOKEN`` env var.
    """

    def __init__(self, access_token: str | None = None) -> None:
        self._token = access_token or os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._req_id = 0
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _ensure_started(self) -> None:
        """Start the MCP server subprocess if not already running."""
        if self._proc is not None and self._proc.returncode is None:
            return

        logger.info("Starting @hubspot/mcp-server subprocess…")
        env = {**os.environ, "HUBSPOT_ACCESS_TOKEN": self._token}

        self._proc = await asyncio.create_subprocess_exec(
            "npx", "--yes", "@hubspot/mcp-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._initialized = False
        await self._initialize()

    async def _initialize(self) -> None:
        """Send the MCP initialize handshake and wait for the ready response."""
        init_msg = {
            "jsonrpc": _JSONRPC_VERSION,
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "conversion-engine", "version": "1.0"},
            },
        }
        await self._send(init_msg)

        # Read until we get the initialize result
        deadline = time.monotonic() + _INIT_TIMEOUT
        while time.monotonic() < deadline:
            line = await asyncio.wait_for(
                self._proc.stdout.readline(),  # type: ignore[union-attr]
                timeout=_INIT_TIMEOUT,
            )
            if not line:
                continue
            try:
                msg = json.loads(line.decode())
                if msg.get("id") == init_msg["id"] and "result" in msg:
                    # Send initialized notification
                    await self._send({
                        "jsonrpc": _JSONRPC_VERSION,
                        "method": "notifications/initialized",
                    })
                    self._initialized = True
                    logger.info("HubSpot MCP server initialized.")
                    return
            except json.JSONDecodeError:
                continue

        raise RuntimeError("HubSpot MCP server did not initialize within timeout.")

    async def close(self) -> None:
        """Terminate the MCP server subprocess."""
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            await self._proc.wait()
            logger.info("HubSpot MCP server stopped.")

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute a HubSpot MCP tool call.

        Args:
            tool_name: The tool name (e.g. ``"hubspot_upsert_contact"``).
            arguments: Tool arguments matching the tool's input schema.

        Returns:
            The parsed ``content`` from the MCP tool result.

        Raises:
            RuntimeError: If the server returns an error or times out.
        """
        async with self._lock:
            await self._ensure_started()

            req_id = self._next_id()
            request = {
                "jsonrpc": _JSONRPC_VERSION,
                "id": req_id,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }

            await self._send(request)
            response = await self._read_response(req_id)

        if "error" in response:
            err = response["error"]
            raise RuntimeError(
                f"MCP tool '{tool_name}' error {err.get('code')}: {err.get('message')}"
            )

        result = response.get("result", {})
        content = result.get("content", [])

        # Extract text from content blocks
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            combined = "\n".join(texts)
            # Try to parse as JSON for structured results
            try:
                return json.loads(combined)
            except (json.JSONDecodeError, ValueError):
                return combined

        return content

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        """Return the next monotonically increasing request ID."""
        self._req_id += 1
        return self._req_id

    async def _send(self, msg: dict[str, Any]) -> None:
        """Write a JSON-RPC message to the server's stdin.

        Args:
            msg: The JSON-RPC message dict to send.
        """
        line = json.dumps(msg) + "\n"
        self._proc.stdin.write(line.encode())  # type: ignore[union-attr]
        await self._proc.stdin.drain()  # type: ignore[union-attr]

    async def _read_response(self, req_id: int) -> dict[str, Any]:
        """Read lines from stdout until we find the response for req_id.

        Args:
            req_id: The request ID to match.

        Returns:
            The parsed JSON-RPC response dict.

        Raises:
            asyncio.TimeoutError: If no matching response arrives within timeout.
            RuntimeError: If the server process exits unexpectedly.
        """
        deadline = time.monotonic() + _CALL_TIMEOUT
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                line = await asyncio.wait_for(
                    self._proc.stdout.readline(),  # type: ignore[union-attr]
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(
                    f"MCP tool call timed out after {_CALL_TIMEOUT}s (req_id={req_id})"
                )

            if not line:
                if self._proc.returncode is not None:
                    raise RuntimeError(
                        f"MCP server exited unexpectedly (code={self._proc.returncode})"
                    )
                continue

            try:
                msg = json.loads(line.decode())
            except json.JSONDecodeError:
                logger.debug("MCP non-JSON line: %s", line[:200])
                continue

            if msg.get("id") == req_id:
                return msg

            # Log unexpected messages (notifications, other responses)
            logger.debug("MCP unexpected message: %s", str(msg)[:200])

        raise asyncio.TimeoutError(f"MCP response timeout for req_id={req_id}")


# ---------------------------------------------------------------------------
# Module-level singleton — shared across the agent
# ---------------------------------------------------------------------------

_client: HubSpotMCPClient | None = None


def get_mcp_client() -> HubSpotMCPClient:
    """Return the module-level MCP client singleton.

    Returns:
        The shared ``HubSpotMCPClient`` instance.
    """
    global _client
    if _client is None:
        _client = HubSpotMCPClient()
    return _client
