"""R3-5 regression: a flood on one election must not delay another. Stubs web3/nostr; run from relayer/: python3 test_fair_queue.py"""
import sys, types, asyncio, json
import os; os.chdir(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ".")
w = types.ModuleType("web3"); 
class W3:
    @staticmethod
    def to_checksum_address(a): return a
w.Web3 = W3; sys.modules["web3"] = w
l = types.ModuleType("web3.logs"); l.DISCARD = None; sys.modules["web3.logs"] = l
c = types.ModuleType("common")
class FakeEth:
    chain_id = 1
    def get_block(self, n): return types.SimpleNamespace(timestamp=0)
c.w3 = None
for n in ["CONFIG_PATH","KINDS","RPC_URL","account","artifact","load_config","revert_reason","send"]: setattr(c, n, None)
c.LOCAL_CHAIN_ID = 31337; c.KINDS = {}
c.connect = lambda *a, **k: types.SimpleNamespace(eth=FakeEth())
c.account = lambda *a, **k: types.SimpleNamespace(address="0x0")
sys.modules["common"] = c
sys.modules["nostr"] = types.ModuleType("nostr")
for m in ["websockets","coincurve","secp256k1"]: sys.modules.setdefault(m, types.ModuleType(m))
src = open("relayer.py").read()
# skip module-level work that needs real artifacts
src = src.replace("TEMPLATES = {", "TEMPLATES = {} and {", 1)
ns = {"__name__": "relayer"}
try:
    exec(compile(src, "relayer.py", "exec"), ns)
except Exception as e:
    print("import failed", repr(e)); raise
order = []
def fake_process(ev):
    import time; time.sleep(0.01); order.append(json.loads(ev["content"])["election"]); return {"ok": True}
ns["process"] = fake_process
nostr = ns["nostr"]
events = [{"id": f"a{i}", "content": json.dumps({"election": "0xATTACK"})} for i in range(50)] + \
         [{"id": f"r{i}", "content": json.dumps({"election": "0xREAL"})} for i in range(3)]
class Pool:
    def __init__(self, relays): pass
    async def subscribe(self, url, flt, queue):
        for e in events: await queue.put(e)
        await asyncio.sleep(3600)
    async def publish(self, ev): pass
nostr.Pool = Pool; nostr.verify = lambda e: True; nostr.new_key = lambda: None
nostr.make_event = lambda *a: {}; nostr.topic = lambda c: "t"; nostr.KIND_REQUEST = 1; nostr.KIND_REPLY = 2
async def run():
    t = asyncio.create_task(ns["serve"]({"chainId": 1, "semaphore": "s", "nostrRelays": ["x"]}))
    await asyncio.sleep(1.2); t.cancel()
asyncio.run(run())
idx = [i for i, e in enumerate(order) if e == "0xREAL"]
print("processed", len(order), "real served at positions", idx)
assert idx and idx[-1] < 10, "real election was starved"
print("ok")
