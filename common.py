"""Shared helpers for the relayer and the deploy/advance scripts.

Contract ABIs/bytecode come from the Foundry build in ../dapp/out (run `forge build`).
"""

import json
import os
from pathlib import Path

from web3 import Web3
from web3.exceptions import ContractCustomError, ContractLogicError

HERE = Path(__file__).resolve().parent
DAPP = HERE.parent / "dapp"
# Which deployment this process works on. Unset = the main network (election.json, .env).
# GOBLETT_NETWORK=testnet uses election.testnet.json and .env.testnet instead, so a
# second relayer can serve the site's /testnet page alongside the main one.
# GOBLETT_NETWORK=mainnet is the main network too, but defaults to Arc's RPC, not anvil.
_NAMED = os.environ.get("GOBLETT_NETWORK", "").strip().lower()
if _NAMED and not _NAMED.isalnum():
    raise SystemExit("GOBLETT_NETWORK must be a simple name such as 'testnet'")
NETWORK = "" if _NAMED in ("main", "mainnet") else _NAMED
_SUFFIX = f".{NETWORK}" if NETWORK else ""
CONFIG_PATH = HERE / f"election{_SUFFIX}.json"
SITE_CONFIG_PATH = HERE.parent / "site" / "public" / f"election{_SUFFIX}.json"
# relayer.env holds what every network shares: one RELAYER_KEY, so the same wallet relays
# on mainnet and testnet. A named network takes only these variables from it.
SHARED_ENV = HERE / "relayer.env"
SHARED_VARS = {"RELAYER_KEY"}
_DEFAULT_RPC = {"mainnet": "https://rpc.mainnet.arc.io", "main": "https://rpc.mainnet.arc.io", "testnet": "https://rpc.testnet.arc.io"}


def _read_env(path: Path, only=None):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v and (only is None or k in only):
                os.environ.setdefault(k, v)


def _load_dotenv():
    """Read KEY=VALUE lines from the gitignored env files into the environment.
    Real environment variables win, then the network's own file, then relayer.env.
    Keeps private keys out of the repo and the shell history."""
    if NETWORK:
        # A named network never reads the main network's .env (its RPC or deployer key);
        # from relayer.env it takes the shared relayer key and nothing else.
        _read_env(HERE / f".env{_SUFFIX}")
        _read_env(SHARED_ENV, only=SHARED_VARS)
    else:
        _read_env(HERE / ".env")
        _read_env(SHARED_ENV)


_load_dotenv()

RPC_URL = os.environ.get("RPC_URL") or _DEFAULT_RPC.get(_NAMED, "http://127.0.0.1:8545")
LOCAL_CHAIN_ID = 31337  # anvil
# Anvil dev keys: PUBLIC, for the local chain only (#1 is skipped: Arc blocklists it).
_ANVIL_KEYS = {
    "DEPLOYER_KEY": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",  # #0
    "RELAYER_KEY": "0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6",  # #9
}

# What a wallet needs to know to add a chain (EIP-3085). Used by the site.
CHAINS = {
    5042: {
        "chainName": "Arc",
        "nativeCurrency": {"name": "USDC", "symbol": "USDC", "decimals": 18},
        "explorerUrl": "https://explorer.arc.io",
    },
    5042002: {
        "chainName": "Arc Testnet",
        "nativeCurrency": {"name": "USDC", "symbol": "USDC", "decimals": 18},
        "explorerUrl": "https://testnet.arcscan.app",
    },
    LOCAL_CHAIN_ID: {
        "chainName": "goblett local (anvil)",
        "nativeCurrency": {"name": "Test Ether", "symbol": "ETH", "decimals": 18},
        "explorerUrl": None,
    },
}


def account(w3: Web3, name: str):
    """The account for DEPLOYER_KEY / RELAYER_KEY. Off the local chain the key must be
    set explicitly: the anvil defaults are public, so anyone could spend from them."""
    from eth_account import Account

    key = os.environ.get(name)
    if not key:
        if w3.eth.chain_id != LOCAL_CHAIN_ID and os.environ.get("ALLOW_DEV_KEYS") != "1":
            where = f"relayer/.env{_SUFFIX}"
            if name in SHARED_VARS:
                where = f"relayer/relayer.env (shared by all networks) or {where}"
            raise SystemExit(
                f"{name} is not set. On chain {w3.eth.chain_id} you must use your own funded key: put\n"
                f"  {name}=0x...\nin {where} (gitignored). The built-in anvil keys are public.\n"
                f"(For a local fork of a real chain, set ALLOW_DEV_KEYS=1.)"
            )
        key = _ANVIL_KEYS[name]
    return Account.from_key(key)


def fees(w3: Web3) -> dict:
    """EIP-1559 fee fields, set explicitly. maxFee = 2 * base fee + tip leaves room for
    the base fee to rise while the transaction waits; only base + tip is actually paid.
    (Arc silently drops transactions below its 20 gwei minimum base fee.)"""
    base = w3.eth.get_block("latest").get("baseFeePerGas")
    if base is None:  # pre-1559 chain
        return {"gasPrice": w3.eth.gas_price}
    try:
        tip = w3.eth.max_priority_fee
    except Exception:
        tip = w3.to_wei(1, "gwei")
    return {"maxFeePerGas": 2 * base + tip, "maxPriorityFeePerGas": tip}


def expected_gas_price(w3: Web3) -> int:
    """What a transaction sent now should pay per gas (base fee + tip)."""
    f = fees(w3)
    if "gasPrice" in f:
        return f["gasPrice"]
    return w3.eth.get_block("latest")["baseFeePerGas"] + f["maxPriorityFeePerGas"]


KINDS = {"fptp": "FPTPElection", "ranked": "RankedChoiceElection"}


def artifact(name: str) -> dict:
    path = DAPP / "out" / f"{name}.sol" / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"missing {path}; run `forge build` in dapp/")
    return json.loads(path.read_text())


def connect(rpc_url: str = RPC_URL) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        raise SystemExit(f"cannot reach RPC at {rpc_url} (is anvil running?)")
    return w3


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"{CONFIG_PATH.name} not found; run setup_chain.py first")
    return json.loads(CONFIG_PATH.read_text())


def _error_selectors() -> dict:
    """4-byte selector -> error name, for Election + Semaphore custom errors."""
    out = {}
    for name in ("FPTPElection", "RankedChoiceElection", "Semaphore"):
        for item in artifact(name)["abi"]:
            if item["type"] == "error":
                sig = f"{item['name']}({','.join(i['type'] for i in item['inputs'])})"
                out[Web3.keccak(text=sig)[:4].hex().removeprefix("0x")] = item["name"]
    return out


_SELECTORS = None


def revert_reason(e: Exception) -> str:
    """Turn a web3 revert into the contract's custom error name when possible."""
    global _SELECTORS
    if _SELECTORS is None:
        _SELECTORS = _error_selectors()
    data = getattr(e, "data", None)
    if isinstance(e, (ContractCustomError, ContractLogicError)) and isinstance(data, str):
        sel = data.removeprefix("0x")[:8]
        if sel in _SELECTORS:
            return _SELECTORS[sel]
    msg = str(e.args[0]) if e.args else str(e)
    return msg[:200]


_next_nonce: dict[str, int] = {}  # address -> next nonce this process will use


def _nonce(w3: Web3, address: str) -> int:
    """The RPC's pending nonce, but never below one this process already used. Load-balanced
    public RPCs (Arc's) can answer from a node that hasn't seen our last block yet, which
    hands back a stale nonce right after a receipt and gets the tx rejected as 'nonce too low'."""
    return max(w3.eth.get_transaction_count(address, "pending"), _next_nonce.get(address, 0))


def send(w3: Web3, account, fn, gas_cap: int = 0, precheck=None, value: int = 0) -> dict:
    """Simulate (estimate_gas reverts with the reason), then sign, send, and wait.
    `precheck(gas, gas_price)` may raise to abort before anything is sent."""
    gas = fn.estimate_gas({"from": account.address, "value": value})
    if gas_cap and gas > gas_cap:
        raise ValueError(f"needs {gas} gas, over the relayer's limit")
    if precheck:
        precheck(gas, expected_gas_price(w3))
    nonce = _nonce(w3, account.address)
    tx = fn.build_transaction(
        {
            "from": account.address,
            "nonce": nonce,
            "gas": int(gas * 1.2),
            "value": value,
            **fees(w3),
        }
    )
    signed = account.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    _next_nonce[account.address] = nonce + 1
    return w3.eth.wait_for_transaction_receipt(h, timeout=180)
