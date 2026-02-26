import asyncio
import json
import os
import shlex
import tempfile
import time
import urllib.parse
import requests
import websockets
import discord
from discord.ext import commands

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from identity import MEPIdentity

HUB_URL = os.getenv("HUB_URL", "https://mep-hub.silentcopilot.ai")
WS_URL = os.getenv("WS_URL", "wss://mep-hub.silentcopilot.ai")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DEFAULT_BOUNTY = float(os.getenv("MEP_DEFAULT_BOUNTY", "5.0"))
BOT_KEY_PATH = os.getenv("MEP_BOT_KEY_PATH", os.path.join(tempfile.gettempdir(), "mep_discord_bot.pem"))


class MEPClient:
    def __init__(self, key_path: str):
        self.identity = MEPIdentity(key_path)
        self.node_id = self.identity.node_id
        self.session = requests.Session()
        self.task_channels: dict[str, int] = {}
        self._stop = asyncio.Event()

    async def register(self) -> dict:
        response = await asyncio.to_thread(
            self.session.post,
            f"{HUB_URL}/register",
            json={"pubkey": self.identity.pub_pem},
            timeout=10
        )
        response.raise_for_status()
        return response.json()

    def _auth_headers(self, payload_str: str) -> dict:
        headers = self.identity.get_auth_headers(payload_str)
        headers["Content-Type"] = "application/json"
        return headers

    async def submit_task(self, payload: str, bounty: float, model_requirement: str | None, target_node: str | None) -> dict:
        body: dict = {
            "consumer_id": self.node_id,
            "payload": payload,
            "bounty": bounty
        }
        if model_requirement is not None:
            body["model_requirement"] = model_requirement
        if target_node is not None:
            body["target_node"] = target_node
        payload_str = json.dumps(body)
        headers = self._auth_headers(payload_str)
        response = await asyncio.to_thread(
            self.session.post,
            f"{HUB_URL}/tasks/submit",
            data=payload_str,
            headers=headers,
            timeout=20
        )
        return {"status_code": response.status_code, "json": response.json()}

    async def cancel_task(self, task_id: str) -> dict:
        body = {"task_id": task_id}
        payload_str = json.dumps(body)
        headers = self._auth_headers(payload_str)
        response = await asyncio.to_thread(
            self.session.post,
            f"{HUB_URL}/tasks/cancel",
            data=payload_str,
            headers=headers,
            timeout=20
        )
        return {"status_code": response.status_code, "json": response.json()}

    async def get_result(self, task_id: str) -> dict:
        payload_str = ""
        headers = self._auth_headers(payload_str)
        response = await asyncio.to_thread(
            self.session.get,
            f"{HUB_URL}/tasks/result/{task_id}",
            headers=headers,
            timeout=20
        )
        return {"status_code": response.status_code, "json": response.json()}

    async def get_balance(self) -> dict:
        payload_str = ""
        headers = self._auth_headers(payload_str)
        response = await asyncio.to_thread(
            self.session.get,
            f"{HUB_URL}/balance/{self.node_id}",
            headers=headers,
            timeout=20
        )
        return {"status_code": response.status_code, "json": response.json()}

    async def listen_results(self, on_result):
        while not self._stop.is_set():
            ts = str(int(time.time()))
            sig = urllib.parse.quote(self.identity.sign(self.node_id, ts))
            uri = f"{WS_URL}/ws/{self.node_id}?timestamp={ts}&signature={sig}"
            try:
                async with websockets.connect(uri) as ws:
                    while not self._stop.is_set():
                        msg = await ws.recv()
                        data = json.loads(msg)
                        if data.get("event") == "task_result":
                            await on_result(data["data"])
            except Exception:
                await asyncio.sleep(2)


def parse_task_args(text: str):
    tokens = shlex.split(text)
    bounty = DEFAULT_BOUNTY
    model = "cli-agent"
    target = None
    payload_parts: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("--bounty", "-b") and i + 1 < len(tokens):
            i += 1
            bounty = float(tokens[i])
        elif token == "--model" and i + 1 < len(tokens):
            i += 1
            model = tokens[i]
        elif token == "--target" and i + 1 < len(tokens):
            i += 1
            target = tokens[i]
        else:
            payload_parts.append(token)
        i += 1
    payload = " ".join(payload_parts).strip()
    return payload, bounty, model, target


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
client = MEPClient(BOT_KEY_PATH)


@bot.event
async def on_ready():
    if DISCORD_TOKEN is None:
        return
    await client.register()

    async def on_result(data: dict):
        task_id = data["task_id"]
        channel_id = client.task_channels.get(task_id)
        if channel_id is None:
            return
        channel = bot.get_channel(channel_id)
        if channel is None:
            return
        result = data.get("result_payload", "")
        await channel.send(f"Completed task {task_id}: {result}")

    bot.loop.create_task(client.listen_results(on_result))


@bot.command(name="mep")
async def mep(ctx, *, text: str):
    payload, bounty, model, target = parse_task_args(text)
    if not payload:
        await ctx.send("Usage: !mep <task> [--bounty 5.0] [--model cli-agent] [--target node_id]")
        return
    response = await client.submit_task(payload, bounty, model, target)
    data = response["json"]
    if response["status_code"] != 200:
        await ctx.send(f"Submit failed: {data}")
        return
    task_id = data.get("task_id")
    if task_id:
        client.task_channels[task_id] = ctx.channel.id
    await ctx.send(f"Submitted task {task_id} to MEP Hub")


@bot.command(name="mepdm")
async def mepdm(ctx, target_node: str, *, message: str):
    response = await client.submit_task(message, 0.0, None, target_node)
    data = response["json"]
    if response["status_code"] != 200:
        await ctx.send(f"DM failed: {data}")
        return
    task_id = data.get("task_id")
    if task_id:
        client.task_channels[task_id] = ctx.channel.id
    await ctx.send(f"Sent DM task {task_id} to {target_node}")


@bot.command(name="mepdata")
async def mepdata(ctx, price: float, *, payload: str):
    bounty = -abs(price)
    response = await client.submit_task(payload, bounty, None, None)
    data = response["json"]
    if response["status_code"] != 200:
        await ctx.send(f"Data offer failed: {data}")
        return
    task_id = data.get("task_id")
    if task_id:
        client.task_channels[task_id] = ctx.channel.id
    await ctx.send(f"Offered data task {task_id} for {bounty} SECONDS")


@bot.command(name="mepcancel")
async def mepcancel(ctx, task_id: str):
    response = await client.cancel_task(task_id)
    data = response["json"]
    if response["status_code"] != 200:
        await ctx.send(f"Cancel failed: {data}")
        return
    await ctx.send(f"Cancelled task {task_id} — bounty refunded")


@bot.command(name="mepresult")
async def mepresult(ctx, task_id: str):
    response = await client.get_result(task_id)
    data = response["json"]
    if response["status_code"] != 200:
        await ctx.send(f"Result lookup failed: {data}")
        return
    await ctx.send(f"Result for {task_id}: {data.get('result_payload')}")


@bot.command(name="mepbalance")
async def mepbalance(ctx):
    response = await client.get_balance()
    data = response["json"]
    if response["status_code"] != 200:
        await ctx.send(f"Balance lookup failed: {data}")
        return
    balance = data.get("balance_seconds")
    await ctx.send(f"Balance for {client.node_id}: {balance} SECONDS")


if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
