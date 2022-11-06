#!/usr/bin/env python3

import logging

from itertools import count
from contextlib import asynccontextmanager as acm, aclosing

import trio
import httpx

from msgspec import Struct

from eth_utils import to_int


class BlockNotFound(BaseException):
    ...


class JSONRPCResult(Struct):
    jsonrpc: str = '2.0'
    id: int
    result: dict | None = None
    error: dict | None = None


class AsyncWeb3:

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self._client = httpx.AsyncClient()
        self._rpc_id: Iterable = count(0)

    async def __aenter__(self):
        self._client = self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._client.__aexit__(exc_type, exc, tb)

    async def json_rpc(self, method: str, params: list = []) -> dict:
        resp = (await self._client.post(
            self.endpoint,
            json={
                'jsonrpc': '2.0',
                'method': method,
                'params': params,
                'id': next(self._rpc_id)
            }
        )).json()

        resp = JSONRPCResult(**resp)
        if resp.error:
            raise ValueError(resp)

        return resp

    async def chain_id(self):
        return (await self.json_rpc('eth_chainId')).result

    async def block_number(self):
        return int((await self.json_rpc('eth_blockNumber')).result, 0)

    async def get_block(
        self,
        block_num: int | str = 'latest',
        full_transactions: bool = True
    ):
        return (await self.json_rpc(
            'eth_getBlockByNumber',
            [block_num, full_transactions])).result

    async def _stream_blocks(
        self,
        # can be 'latest', 'earliest', 'pending', a block number or a hash
        start_block: str | int = 'latest',
        end_block: str | int | None = None,
        full: bool = True,
        max_tasks: int = 2000
    ):
        start_block = await self.get_block(start_block, full_transactions=full)

        yield start_block

        start_block = to_int(hexstr=start_block['number'])

        if end_block:
            end_block = to_int(
                hexstr=(await self.get_block(end_block, full_transactions=full))['number'])
        else:
            end_block = 2 ** 64

        head_block = await self.block_number()
        need_head_updates = end_block > head_block

        async def head_block_updater():
            while need_head_updates:
                await trio.sleep(2)
                head_block = await self.block_number()

        send_channel, receive_channel = trio.open_memory_channel(0)
        async def block_task(block_number, event):
            for i in range(5):
                try:
                    block = await self.get_block(block_number, full_transactions=full)
                    break

                    if to_int(hexstr=block['timestamp']) == 0:
                        raise BlockNotFound('timestamp == 0 in block')

                except BlockNotFound:
                    await trio.sleep(.5)

            await send_channel.send(block)
            event.set()

        current_block = start_block
        async with trio.open_nursery() as n:

            n.start_soon(head_block_updater)

            async def block_task_spawner():
                nonlocal max_tasks
                nonlocal current_block
                tasks = []
                async with send_channel:
                    while current_block != end_block:

                        if head_block - current_block <= 3:
                            need_head_updates = False
                            max_tasks = 3

                        next_block_num = current_block + 1

                        if len(tasks) > max_tasks:
                            await tasks[0].wait()
                            tasks.pop(0)

                        event = trio.Event()
                        tasks.append(event)
                        n.start_soon(block_task, next_block_num, event)

                        current_block = next_block_num

                    for task in tasks:
                        await task.wait()


            n.start_soon(block_task_spawner)

            looking_for = start_block + 1
            pending = {}
            async with receive_channel:
                async for block in receive_channel:
                    pending[to_int(hexstr=block['number'])] = block

                    while looking_for in pending:
                        yield pending.pop(looking_for)
                        if looking_for == end_block:
                            break
                        looking_for += 1

            # just to be sure
            need_head_updates = False

    @acm
    async def stream_blocks(self, *args, **kwargs):
        async with aclosing(
            self._stream_blocks(*args, **kwargs)
        ) as wrapped_stream:
            yield wrapped_stream
