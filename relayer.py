"""goblett relayer: reads registrations and votes from a Nostr topic and forwards
them to the election contract, paying the gas.

Transport (see nostr.py): voters publish a kind-9467 event tagged
["t", "goblett-<chainId>"] to public Nostr relays. Its content is JSON:

    {"type": "register", "election", "wallet", "identityCommitment", "signature"}   -> registerFor
    {"type": "vote", "election", "proof": {...}, "ranking": [..]?}                   -> vote

The relayer serves ANY genuine goblett election on its chain, so elections created from
the website need no relayer configuration. "Genuine" = the contract's code is exactly
our FPTPElection / RankedChoiceElection build and it uses our Semaphore (see
lookup_election). Without that check anyone could make the relayer pay for calls to
arbitrary contracts.

The relayer answers with a kind-9468 event tagged ["e", <request id>] whose content is
{"ok", "txHash"?, "error"?}. That reply is a courtesy; voters should check the chain.

The relayer can't forge or alter anything: registerFor checks the wallet's EIP-712
signature, and vote() checks the Semaphore proof, which binds the ballot. Nostr events
are signed with throwaway keys, so the Nostr signature authorizes nothing. Anyone can
run another relayer on the same topic; whoever submits first wins and the rest see
AlreadyRegistered / NullifierAlreadyUsed in simulation (no gas spent).

Payment: each relay credits this relayer inside the election contract (gas refund +
bounty), funded by the election's creator. Collect it with withdraw.py. By default (off
the local chain) relays the election would not pay back are refused.

Privacy: the relayer never sees a voter's IP; the Nostr relays do instead (plus
timing). Use Tor/VPN for real elections.

Env (or relayer/.env): RPC_URL, RELAYER_KEY, REQUIRE_REIMBURSEMENT.
"""

import asyncio
import collections
import json
import os
import threading
import time

from web3 import Web3
from web3.logs import DISCARD

import nostr
from common import CONFIG_PATH, KINDS, LOCAL_CHAIN_ID, RPC_URL, account, artifact, connect, load_config, revert_reason, send

MAX_CONTENT = 16 * 1024
U256 = 2**256

MAX_GAS = 3_000_000  # a register is ~200k and a vote ~400k; refuse anything far beyond

w3 = connect(RPC_URL)
relayer = account(w3, "RELAYER_KEY")
# Refuse relays the election would not pay back (unfunded, or its gas price cap is below
# the going rate). On by default except on the local chain; REQUIRE_REIMBURSEMENT=0/1 overrides.
REQUIRE_REIMBURSEMENT = os.environ.get("REQUIRE_REIMBURSEMENT", "0" if w3.eth.chain_id == LOCAL_CHAIN_ID else "1") == "1"
tx_lock = threading.Lock()  # one tx at a time, so nonces never collide
cfg = None
elections = {}  # address -> (contract, kind), for addresses that passed lookup_election
rejected = {}  # address -> unix time until which it is known not to be an election (R3-5)
REJECT_TTL = 600
# R3-5: requests are queued per election and served round-robin, so one election (even a
# genuine, funded one) can't starve the others. Elections listed in PRIORITY_ELECTIONS
# (comma list; the configured default election is always included) get more turns.
PRIORITY_WEIGHT = int(os.environ.get("PRIORITY_WEIGHT", "4"))
MAX_PENDING_PER_ELECTION = int(os.environ.get("MAX_PENDING_PER_ELECTION", "200"))


def _masked_runtime(code: bytes, art: dict) -> bytes:
    """Runtime bytecode with the immutable slots zeroed. Immutables (schedule, scope,
    semaphore address, ...) are baked into the deployed code, so two elections differ
    there and only there."""
    code = bytearray(code)
    for refs in art["deployedBytecode"].get("immutableReferences", {}).values():
        for r in refs:
            code[r["start"] : r["start"] + r["length"]] = bytes(r["length"])
    return bytes(code)


TEMPLATES = {
    kind: (art, _masked_runtime(bytes.fromhex(art["deployedBytecode"]["object"].removeprefix("0x")), art))
    for kind, art in ((k, artifact(name)) for k, name in KINDS.items())
}


def lookup_election(addr) -> tuple:
    """Return (contract, kind) if `addr` is a genuine goblett election on this chain."""
    try:
        addr = Web3.to_checksum_address(addr)
    except (TypeError, ValueError):
        raise BadRequest("need election (address)")
    if addr in elections:
        return elections[addr]
    if rejected.get(addr, 0) > time.time():  # R3-5: don't pay an RPC call per junk event
        raise BadRequest("not a goblett election contract")
    code = bytes(w3.eth.get_code(addr))
    for kind, (art, template) in TEMPLATES.items():
        if len(code) == len(template) and _masked_runtime(code, art) == template:
            contract = w3.eth.contract(address=addr, abi=art["abi"])
            if contract.functions.semaphore().call() != Web3.to_checksum_address(cfg["semaphore"]):
                raise BadRequest("election uses an unknown Semaphore contract")
            elections[addr] = (contract, kind)
            print(f"  serving election {addr} ({kind}): {contract.functions.title().call()!r}")
            return elections[addr]
    rejected[addr] = time.time() + REJECT_TTL
    raise BadRequest("not a goblett election contract")


class BadRequest(Exception):
    pass


def uint(x, name) -> int:
    try:
        v = int(x, 0) if isinstance(x, str) else int(x)
    except (TypeError, ValueError):
        raise BadRequest(f"{name} must be an integer")
    if isinstance(x, bool) or not 0 <= v < U256:
        raise BadRequest(f"{name} out of range")
    return v


def handle_register(body: dict) -> dict:
    try:
        wallet = Web3.to_checksum_address(body["wallet"])
        sig = bytes.fromhex(str(body["signature"]).removeprefix("0x"))
    except (KeyError, ValueError):
        raise BadRequest("need wallet (address) and signature (hex)")
    commitment = uint(body.get("identityCommitment"), "identityCommitment")
    election, _ = lookup_election(body.get("election"))
    fn = election.functions.registerFor(wallet, commitment, sig)
    return submit(election, fn, f"registerFor {wallet}", max_refund_gas=300_000)


def handle_vote(body: dict) -> dict:
    p = body.get("proof")
    if not isinstance(p, dict) or not isinstance(p.get("points"), list) or len(p["points"]) != 8:
        raise BadRequest("proof must have merkleTreeDepth, merkleTreeRoot, nullifier, message, scope, points[8]")
    proof = (
        uint(p.get("merkleTreeDepth"), "merkleTreeDepth"),
        uint(p.get("merkleTreeRoot"), "merkleTreeRoot"),
        uint(p.get("nullifier"), "nullifier"),
        uint(p.get("message"), "message"),
        uint(p.get("scope"), "scope"),
        [uint(x, "points") for x in p["points"]],
    )
    election, kind = lookup_election(body.get("election"))
    if kind == "ranked":
        r = body.get("ranking")
        if not isinstance(r, list) or not 1 <= len(r) <= 32:
            raise BadRequest("ranked election: ranking must be a list of 1..32 candidate indices")
        fn = election.functions.vote(proof, [uint(x, "ranking") for x in r])
    else:
        fn = election.functions.vote(proof)
    # Log only the nullifier: it is public on-chain anyway and links to nothing.
    return submit(election, fn, f"vote nullifier {hex(proof[2])[:14]}...")


def reimbursement_check(election, max_refund_gas=1_000_000):
    """A precheck for send(): raise unless the election will pay back this relay.
    Mirrors Election._creditRelayer: min(funds, min(gas + overhead, max_refund_gas) * min(price, cap) + bounty);
    max_refund_gas is MAX_REGISTER_REFUND_GAS (300k) for registerFor, MAX_REFUND_GAS (1M) for votes."""
    f = election.functions
    bounty, cap, funds = f.relayBounty().call(), f.maxRefundGasPrice().call(), f.relayFunds().call()

    def check(gas: int, gas_price: int):
        cost = gas * gas_price
        credit = min(funds, min(gas + 40_000, max_refund_gas) * min(gas_price, cap) + bounty)
        if credit < cost:
            why = "it has no relayer funding left" if funds < cost else f"its gas price cap ({cap / 1e9:g} gwei) is below the current price"
            raise BadRequest(f"this relayer would not be reimbursed: {why}")

    return check


def submit(election, fn, label: str, max_refund_gas: int = 1_000_000) -> dict:
    with tx_lock:
        try:
            precheck = reimbursement_check(election, max_refund_gas) if REQUIRE_REIMBURSEMENT else None
            rcpt = send(w3, relayer, fn, gas_cap=MAX_GAS, precheck=precheck)
        except BadRequest:
            raise
        except Exception as e:  # revert during estimate_gas, or RPC failure
            reason = revert_reason(e)
            print(f"  rejected {label}: {reason}")
            return {"ok": False, "error": reason}
    ok = rcpt.status == 1
    credited = sum(ev["args"]["amount"] for ev in election.events.RelayerCredited().process_receipt(rcpt, errors=DISCARD))
    cost = rcpt.gasUsed * rcpt.effectiveGasPrice
    print(
        f"  {'sent' if ok else 'FAILED'} {label}: tx {rcpt.transactionHash.to_0x_hex()} gas {rcpt.gasUsed},"
        f" paid {cost / 1e18:.6f}, credited {credited / 1e18:.6f}"
    )
    return {"ok": ok, "txHash": rcpt.transactionHash.to_0x_hex(), "gasUsed": rcpt.gasUsed}


HANDLERS = {"register": handle_register, "vote": handle_vote}


def process(event: dict) -> dict:
    """Blocking: validate one request event and forward it to the chain."""
    try:
        if len(event["content"]) > MAX_CONTENT:
            raise BadRequest("content too large")
        body = json.loads(event["content"])
        handler = HANDLERS.get(body.get("type")) if isinstance(body, dict) else None
        if not handler:
            raise BadRequest('content must be JSON with type "register" or "vote"')
        return handler(body)
    except (BadRequest, json.JSONDecodeError) as e:
        print(f"  bad request {event['id'][:12]}: {e}")
        return {"ok": False, "error": str(e)}


async def serve(config: dict):
    """Subscribe to the election's topic on every relay and forward what arrives."""
    global cfg
    cfg = config
    elections.clear()
    topic = nostr.topic(cfg["chainId"])
    print(f"listening on topic {topic} (Semaphore {cfg['semaphore']})")

    key = nostr.new_key()  # throwaway identity for replies
    seen = set()
    queue = asyncio.Queue()
    pool = nostr.Pool(cfg["nostrRelays"])
    # Catch up on what we missed while offline, but never on events older than the chain:
    # after a local chain restart a new election can reuse an old address, and old
    # registration signatures for it would still verify.
    genesis = w3.eth.get_block(0).timestamp
    flt = {"kinds": [nostr.KIND_REQUEST], "#t": [topic], "since": max(genesis, int(time.time()) - 24 * 3600)}
    tasks = [asyncio.create_task(pool.subscribe(url, flt, queue)) for url in cfg["nostrRelays"]]
    priority = {a.strip().lower() for a in os.environ.get("PRIORITY_ELECTIONS", "").split(",") if a.strip()}
    if cfg.get("election"):
        priority.add(str(cfg["election"]).lower())
    buckets = {}  # election (lowercase str) -> deque of events, in arrival order
    ready = asyncio.Event()

    def bucket_key(event) -> str:
        try:
            body = json.loads(event["content"]) if len(event["content"]) <= MAX_CONTENT else None
            return str(body.get("election", "")).lower() if isinstance(body, dict) else ""
        except (ValueError, TypeError):
            return ""

    async def intake():
        while True:
            event = await queue.get()
            if event["id"] in seen or not nostr.verify(event):
                continue
            seen.add(event["id"])
            q = buckets.setdefault(bucket_key(event), collections.deque())
            if len(q) >= MAX_PENDING_PER_ELECTION:
                q.popleft()  # drop the oldest; that election is flooding
            q.append(event)
            ready.set()

    async def handle(event):
        try:
            result = await asyncio.to_thread(process, event)
        except Exception as e:  # never let one bad message stop the relayer
            print(f"  error on {event['id'][:12]}: {e!r}")
            result = {"ok": False, "error": "internal relayer error"}
        reply = nostr.make_event(key, nostr.KIND_REPLY, [["t", topic], ["e", event["id"]]], json.dumps(result))
        await pool.publish(reply)

    tasks.append(asyncio.create_task(intake()))
    try:
        while True:
            await ready.wait()
            ready.clear()
            while any(buckets.values()):
                # One round: priority elections first, each election gets its turns.
                for k in sorted(list(buckets), key=lambda k: k not in priority):
                    q = buckets.get(k)
                    for _ in range(PRIORITY_WEIGHT if k in priority else 1):
                        if not q:
                            break
                        await handle(q.popleft())
                    if not q:
                        buckets.pop(k, None)
    finally:
        for t in tasks:
            t.cancel()


async def main():
    bal = w3.eth.get_balance(relayer.address)
    print(f"goblett relayer (Nostr): chain {w3.eth.chain_id} via {RPC_URL}")
    print(f"  relayer wallet {relayer.address}, balance {Web3.from_wei(bal, 'ether')} (native)")
    while True:  # restart the subscription whenever election.json changes (redeploy)
        try:
            config = load_config()
            if w3.eth.chain_id != config["chainId"]:
                raise ValueError(f"RPC chain {w3.eth.chain_id} != election.json chain {config['chainId']}")
            mtime = CONFIG_PATH.stat().st_mtime
        except (OSError, ValueError, KeyError) as e:  # e.g. file half-written during a redeploy
            print(f"  config not usable yet ({e}); retrying")
            await asyncio.sleep(2)
            continue
        task = asyncio.create_task(serve(config))
        while not task.done():
            await asyncio.sleep(2)
            try:
                if CONFIG_PATH.stat().st_mtime != mtime:
                    break
            except OSError:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:  # keep running whatever happens
            print(f"  subscription crashed ({e!r}); restarting")
            await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
