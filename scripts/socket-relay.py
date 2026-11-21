#!/usr/bin/env python3
"""Relay local TCP streams over a shared Unix socket.

This is used only by the isolated offline acceptance environment. Service
containers have Docker networking disabled and expose their loopback APIs
through Unix sockets. The automation container converts those sockets back to
its own private loopback addresses.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path


StreamPair = tuple[asyncio.StreamReader, asyncio.StreamWriter]
Connector = Callable[[], Awaitable[StreamPair]]


async def bridge(
    source_reader: asyncio.StreamReader,
    source_writer: asyncio.StreamWriter,
    connector: Connector,
) -> None:
    try:
        target_reader, target_writer = await connector()
    except Exception:
        source_writer.close()
        await source_writer.wait_closed()
        return

    async def copy(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        while data := await reader.read(1024 * 1024):
            writer.write(data)
            await writer.drain()

    tasks = {
        asyncio.create_task(copy(source_reader, target_writer)),
        asyncio.create_task(copy(target_reader, source_writer)),
    }
    try:
        _, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        target_writer.close()
        source_writer.close()
        await asyncio.gather(
            target_writer.wait_closed(),
            source_writer.wait_closed(),
            return_exceptions=True,
        )


async def serve_unix_to_tcp(args: argparse.Namespace) -> None:
    path = Path(args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_socket():
        mode = path.lstat().st_mode
        if not stat.S_ISSOCK(mode):
            raise RuntimeError(f"refusing to replace non-socket path: {path}")
        path.unlink()

    async def connect() -> StreamPair:
        return await asyncio.open_connection(args.target_host, args.target_port)

    server = await asyncio.start_unix_server(
        lambda reader, writer: bridge(reader, writer, connect),
        path=str(path),
    )
    os.chmod(path, 0o666)
    await run_server(server)


async def serve_tcp_to_unix(args: argparse.Namespace) -> None:
    if args.listen_host not in {"127.0.0.1", "localhost"}:
        raise ValueError("TCP relay must listen on loopback")

    async def connect() -> StreamPair:
        return await asyncio.open_unix_connection(args.path)

    server = await asyncio.start_server(
        lambda reader, writer: bridge(reader, writer, connect),
        host=args.listen_host,
        port=args.listen_port,
    )
    await run_server(server)


async def run_server(server: asyncio.AbstractServer) -> None:
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    async with server:
        await stopped.wait()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)

    unix = subparsers.add_parser("unix-listen")
    unix.add_argument("--path", required=True)
    unix.add_argument("--target-host", default="127.0.0.1")
    unix.add_argument("--target-port", type=int, required=True)

    tcp = subparsers.add_parser("tcp-listen")
    tcp.add_argument("--listen-host", default="127.0.0.1")
    tcp.add_argument("--listen-port", type=int, required=True)
    tcp.add_argument("--path", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "unix-listen":
        asyncio.run(serve_unix_to_tcp(args))
    else:
        asyncio.run(serve_tcp_to_unix(args))


if __name__ == "__main__":
    main()
