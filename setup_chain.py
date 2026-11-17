"""One-time setup per chain: make sure Semaphore is deployed, write the shared config
(relayer/election.json and site/public/election.json), and export the election
contracts' ABI + bytecode for the site (site/contracts.json) so it can deploy
elections through MetaMask.

    python setup_chain.py                       # after starting anvil, or after `forge build`
    python setup_chain.py --fund 0xYourWallet   # local anvil only: give wallets test funds

Elections themselves are created from the website (or with deploy_election.py).
"""

import argparse
import json

from web3 import Web3

from common import CHAINS, CONFIG_PATH, KINDS, LOCAL_CHAIN_ID, RPC_URL, SITE_CONFIG_PATH, account, artifact, connect
from deploy_election import deploy_semaphore
from nostr import DEFAULT_RELAYS

SITE_CONTRACTS_PATH = SITE_CONFIG_PATH.parent.parent / "contracts.json"


def write_config(cfg: dict):
    for path in (CONFIG_PATH, SITE_CONFIG_PATH):
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2))
        try:
            tmp.replace(path)  # atomic: readers never see a half-written file
        except PermissionError:  # Windows: the target is open elsewhere (e.g. the dev server)
            path.write_text(tmp.read_text())
            tmp.unlink()


def export_contracts():
    # deployedBytecode + immutableReferences let the site verify an ?election= contract
    # the same way the relayer's lookup_election does (R3-6).
    out = {
        k: {
            "abi": artifact(n)["abi"],
            "bytecode": artifact(n)["bytecode"]["object"],
            "deployedBytecode": artifact(n)["deployedBytecode"]["object"],
            "immutableReferences": artifact(n)["deployedBytecode"].get("immutableReferences", {}),
        }
        for k, n in KINDS.items()
    }
    SITE_CONTRACTS_PATH.write_text(json.dumps(out))


def _is_current_election(w3, addr) -> bool:
    """True if `addr` is an election from the current contract build (older builds lack
    newer functions and would break the site)."""
    try:
        w3.eth.contract(address=Web3.to_checksum_address(addr), abi=artifact("Election")["abi"]).functions.relayFunds().call()
        return True
    except Exception:
        return False


def ensure_chain(w3, rpc_public=None, relays=None, semaphore=None) -> dict:
    """Return the config for this chain, deploying Semaphore if it isn't there."""
    chain_id = w3.eth.chain_id
    old = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    if old.get("chainId") != chain_id:
        old = {}
    sem = semaphore or old.get("semaphore")
    if not sem or not w3.eth.get_code(Web3.to_checksum_address(sem)):
        sem = deploy_semaphore(w3, account(w3, "DEPLOYER_KEY"))
        old = {}  # a fresh chain: any remembered election is gone
    cfg = {
        "chainId": chain_id,
        "rpcUrl": rpc_public or old.get("rpcUrl") or w3.provider.endpoint_uri,
        "semaphore": Web3.to_checksum_address(sem),
        "nostrRelays": relays or old.get("nostrRelays") or DEFAULT_RELAYS,
        # So the site can add the network to a wallet, show amounts and link to the explorer.
        **CHAINS.get(chain_id, {"chainName": f"chain {chain_id}", "nativeCurrency": {"name": "Native", "symbol": "NATIVE", "decimals": 18}, "explorerUrl": None}),
    }
    # Optional default election (shown when the site is opened without ?election=).
    if old.get("election") and _is_current_election(w3, old["election"]):
        cfg["election"] = old["election"]
    return cfg


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rpc", default=RPC_URL)
    p.add_argument("--public-rpc", help="RPC URL the browser should use (default: --rpc)")
    p.add_argument("--semaphore", help="existing Semaphore address")
    p.add_argument("--nostr-relays", nargs="+", help=f"default: {DEFAULT_RELAYS}")
    p.add_argument("--fund", nargs="*", default=[], help="LOCAL anvil only: set these wallets' balance to 100")
    a = p.parse_args()

    w3 = connect(a.rpc)
    cfg = ensure_chain(w3, a.public_rpc, a.nostr_relays, a.semaphore)
    write_config(cfg)
    export_contracts()
    if a.fund and cfg["chainId"] != LOCAL_CHAIN_ID:
        raise SystemExit("--fund only works on a local anvil chain; use the faucet on a real chain")
    for addr in a.fund:
        w3.provider.make_request("anvil_setBalance", [Web3.to_checksum_address(addr), hex(100 * 10**18)])
        print(f"funded {addr} with 100 test coins")
    print(f"chain {cfg['chainId']}, Semaphore {cfg['semaphore']}, relays {cfg['nostrRelays']}")
    print(f"wrote {CONFIG_PATH.name}, site/public/{SITE_CONFIG_PATH.name}, site/{SITE_CONTRACTS_PATH.name}")


if __name__ == "__main__":
    print(RPC_URL)
    main()
