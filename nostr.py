"""Just enough Nostr (NIP-01) for the relayer: event ids, BIP-340 signatures, and a
reconnecting subscription per relay.

A Nostr event is JSON {id, pubkey, created_at, kind, tags, content, sig}, where
id = sha256 of the canonical serialization and sig is a Schnorr signature of id.
Relays are dumb websocket servers that store events and forward the ones matching a
subscriber's filter. Our "topic" is a ["t", ...] tag that subscribers filter on.
"""

import asyncio
import hashlib
import json
import os
import time

import websockets
from coincurve import PrivateKey, PublicKeyXOnly

DEFAULT_RELAYS = ["wss://nos.lol", "wss://relay.primal.net"]
# App-specific kinds in the "regular" range (1000-9999): relays store them, so a
# relayer that was offline still gets the messages when it reconnects.
KIND_REQUEST = 9467  # voter -> relayers
KIND_REPLY = 9468  # relayer -> voter


def topic(chain_id: int) -> str:
    """One topic per chain; each message names its election."""
    return f"goblett-{chain_id}"


def event_id(ev: dict) -> str:
    ser = json.dumps(
        [0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(ser.encode()).hexdigest()


def verify(ev: dict) -> bool:
    """Well-formed, id matches the content, signature matches the id."""
    try:
        if event_id(ev) != ev["id"]:
            return False
        return PublicKeyXOnly(bytes.fromhex(ev["pubkey"])).verify(bytes.fromhex(ev["sig"]), bytes.fromhex(ev["id"]))
    except Exception:
        return False


def new_key() -> PrivateKey:
    return PrivateKey(os.urandom(32))


def make_event(key: PrivateKey, kind: int, tags: list, content: str) -> dict:
    ev = {
        "pubkey": key.public_key_xonly.format().hex(),
        "created_at": int(time.time()),
        "kind": kind,
        "tags": tags,
        "content": content,
    }
    ev["id"] = event_id(ev)
    ev["sig"] = key.sign_schnorr(bytes.fromhex(ev["id"])).hex()
    return ev


class Pool:
    """Live connections to a set of relays."""

    def __init__(self, urls):
        self.urls = urls
        self.live = {}  # url -> websocket

    async def subscribe(self, url: str, flt: dict, queue: asyncio.Queue):
        """Push every matching event from one relay onto the queue; reconnect forever."""
        delay = 1
        while True:
            try:
                async with websockets.connect(url, open_timeout=10, max_size=2**20) as ws:
                    self.live[url] = ws
                    await ws.send(json.dumps(["REQ", "goblett", flt]))
                    print(f"  connected to {url}")
                    delay = 1
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg[0] == "EVENT" and isinstance(msg[2], dict):
                            await queue.put(msg[2])
                        elif msg[0] in ("CLOSED", "NOTICE"):
                            print(f"  {url}: {msg}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"  {url} dropped ({type(e).__name__}: {e}); retrying in {delay}s")
            finally:
                self.live.pop(url, None)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def publish(self, event: dict):
        msg = json.dumps(["EVENT", event])
        for url, ws in list(self.live.items()):
            try:
                await ws.send(msg)
            except Exception as e:
                print(f"  could not publish to {url}: {e}")
