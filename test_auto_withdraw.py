"""Auto-withdraw: only after voting ends, only when owed exceeds the gas, and the list of
unpaid elections survives a restart. Stubs web3/nostr; run: python3 test_auto_withdraw.py"""
import json, os, pathlib, sys, tempfile, types

os.chdir(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ".")
w = types.ModuleType("web3")
class W3:
    @staticmethod
    def to_checksum_address(a): return a
w.Web3 = W3; sys.modules["web3"] = w
l = types.ModuleType("web3.logs"); l.DISCARD = None; sys.modules["web3.logs"] = l

NOW = 1000
GAS, PRICE = 30_000, 25 * 10**9  # a withdrawal costs 0.00075
sent = []

class Call:
    def __init__(self, v): self.v = v
    def call(self): return self.v

class Election:
    def __init__(self, address, voting_end, owed):
        self.address, self.voting_end, self.owed = address, voting_end, owed
        self.functions = types.SimpleNamespace(
            votingEnd=lambda: Call(self.voting_end), owed=lambda who: Call(self.owed), withdraw=lambda: self
        )

def fake_send(w3, account, fn, gas_cap=0, precheck=None, value=0):
    if precheck:
        precheck(GAS, PRICE)
    sent.append(fn.address); fn.owed = 0
    return types.SimpleNamespace(status=1, transactionHash=types.SimpleNamespace(to_0x_hex=lambda: "0xtx"))

c = types.ModuleType("common")
for n in ["RPC_URL", "artifact", "load_config"]: setattr(c, n, None)
c.LOCAL_CHAIN_ID = 31337; c.KINDS = {}
c.CONFIG_PATH = pathlib.Path(tempfile.mkdtemp()) / "election.testnet.json"
c.connect = lambda *a, **k: types.SimpleNamespace(eth=types.SimpleNamespace(chain_id=1, get_block=lambda n: types.SimpleNamespace(timestamp=NOW)))
c.account = lambda *a, **k: types.SimpleNamespace(address="0xRELAYER")
c.revert_reason = str; c.send = fake_send
sys.modules["common"] = c
sys.modules["nostr"] = types.ModuleType("nostr")
src = open("relayer.py").read().replace("TEMPLATES = {", "TEMPLATES = {} and {", 1)
ns = {"__name__": "relayer"}
exec(compile(src, "relayer.py", "exec"), ns)

chain = {
    "0xOPEN": Election("0xOPEN", NOW + 60, 10**16),    # voting still open: wait
    "0xDONE": Election("0xDONE", NOW - 1, 10**16),     # ended, owed 0.01: withdraw
    "0xDUST": Election("0xDUST", NOW - 1, 10**12),     # ended, owed less than the gas: wait
    "0xPAID": Election("0xPAID", NOW - 1, 0),          # ended, nothing owed: forget
}
def lookup(addr):
    if addr not in chain: raise ns["BadRequest"]("not a goblett election contract")
    return chain[addr], "fptp"
ns["lookup_election"] = lookup

path = ns["_unpaid_path"]()
assert path.name == "owed.testnet.json", path
path.write_text(json.dumps(list(chain) + ["0xGONE"]))  # as left by an earlier run
ns["load_unpaid"]()
ns["withdraw_finished"](); ns["withdraw_finished"]()
print("withdrawn from", sent, "| still unpaid", sorted(ns["unpaid"]))
assert sent == ["0xDONE"], "must withdraw exactly once, only from the finished election worth it"
assert ns["unpaid"] == {"0xOPEN", "0xDUST"}
assert sorted(json.loads(path.read_text())) == ["0xDUST", "0xOPEN"], "state file not updated"

# a credited relay is remembered on disk
ns["tx_lock"] = __import__("threading").Lock()
new = Election("0xNEW", NOW + 60, 0)
new.events = types.SimpleNamespace(RelayerCredited=lambda: types.SimpleNamespace(process_receipt=lambda r, errors=None: [{"args": {"amount": 5}}]))
def relay_send(w3, account, fn, gas_cap=0, precheck=None, value=0):
    return types.SimpleNamespace(status=1, gasUsed=1, effectiveGasPrice=1, transactionHash=types.SimpleNamespace(to_0x_hex=lambda: "0xtx"))
ns["send"] = relay_send; ns["REQUIRE_REIMBURSEMENT"] = False
ns["submit"](new, object(), "test relay")
assert "0xNEW" in json.loads(path.read_text()), "credited election not saved"
print("ok")
