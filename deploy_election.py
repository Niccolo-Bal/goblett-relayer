"""Deploy a test election from the command line (and Semaphore, if needed), and make it
the site's default election. Elections can also be created from the website.

Example (local anvil, times in minutes from now):
    python deploy_election.py --title "Lunch" --candidates Pizza Sushi Tacos \
        --whitelist 0xYourMetaMaskAddr 0xAnotherAddr --reg 10 --gap 0 --vote 30 --min-anon 1

Semaphore: reused from election.json if it still has code on this chain, or taken from
--semaphore, otherwise deployed fresh (PoseidonT3 + SemaphoreVerifier + Semaphore).
"""

import argparse

from web3 import Web3

from common import KINDS, RPC_URL, account, artifact, connect, expected_gas_price, send


def deploy(w3, acct, art, *args, bytecode=None, value=0):
    c = w3.eth.contract(abi=art["abi"], bytecode=bytecode or art["bytecode"]["object"])
    rcpt = send(w3, acct, c.constructor(*args), value=value)
    if rcpt.status != 1:
        raise SystemExit(f"deploy failed: {rcpt.transactionHash.hex()}")
    return rcpt


def link(art, libs: dict) -> str:
    """Replace Foundry library placeholders with deployed library addresses."""
    code = art["bytecode"]["object"].removeprefix("0x")
    for _file, names in art["bytecode"]["linkReferences"].items():
        for name, refs in names.items():
            addr = libs[name].lower().removeprefix("0x")
            for r in refs:
                s = r["start"] * 2
                code = code[:s] + addr + code[s + 40 :]
    return "0x" + code


def deploy_semaphore(w3, acct) -> str:
    poseidon = deploy(w3, acct, artifact("PoseidonT3")).contractAddress
    verifier = deploy(w3, acct, artifact("SemaphoreVerifier")).contractAddress
    sem_art = artifact("Semaphore")
    sem = deploy(w3, acct, sem_art, verifier, bytecode=link(sem_art, {"PoseidonT3": poseidon}))
    print(f"Semaphore deployed at {sem.contractAddress} (verifier {verifier}, PoseidonT3 {poseidon})")
    return sem.contractAddress


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--kind", choices=KINDS, default="fptp")
    p.add_argument("--title", default="goblett test election")
    p.add_argument("--candidates", nargs="+", default=["Alice", "Bob", "Carol"])
    p.add_argument("--whitelist", nargs="+", required=True, help="voter wallet addresses")
    p.add_argument("--reg", type=float, default=10, help="registration window, minutes")
    p.add_argument("--gap", type=float, default=0, help="freeze between registration and voting, minutes")
    p.add_argument("--vote", type=float, default=30, help="voting window, minutes")
    p.add_argument("--min-anon", type=int, default=None, help="min registrations before voting (default: min(2, whitelist))")
    p.add_argument("--bounty", type=float, default=0.0005, help="paid to the relayer per registration/vote, in the native token")
    p.add_argument("--max-gas-price", type=float, default=None, help="cap on refunded gas price, gwei (default: 2x the current price)")
    p.add_argument("--fund", type=float, default=None, help="relayer funding sent with the deployment, native token (default: an estimate for a full turnout)")
    p.add_argument("--semaphore", help="existing Semaphore address")
    p.add_argument("--rpc", default=RPC_URL)
    p.add_argument("--nostr-relays", nargs="+", help="Nostr relays carrying registrations/votes")
    p.add_argument("--public-rpc", help="RPC URL the browser should use (default: --rpc)")
    a = p.parse_args()

    w3 = connect(a.rpc)
    acct = account(w3, "DEPLOYER_KEY")

    from setup_chain import ensure_chain, export_contracts, write_config

    cfg = ensure_chain(w3, a.public_rpc, a.nostr_relays, a.semaphore)
    sem = cfg["semaphore"]

    whitelist = [Web3.to_checksum_address(x) for x in a.whitelist]
    min_anon = a.min_anon or min(2, len(whitelist))
    # The pending block's time, not the latest block's: on an idle local chain the
    # latest block can be many minutes old, which would put the schedule in the past.
    now = w3.eth.get_block("latest").timestamp
    try:
        now = max(now, w3.eth.get_block("pending").timestamp)
    except Exception:
        pass  # some RPCs (Arc) don't serve the pending block; latest is close enough there
    reg_end = now + int(a.reg * 60)
    vote_start = reg_end + int(a.gap * 60)
    vote_end = vote_start + int(a.vote * 60)

    name = KINDS[a.kind]
    bounty = w3.to_wei(a.bounty, "ether")
    price = expected_gas_price(w3)
    max_gas_price = w3.to_wei(a.max_gas_price, "gwei") if a.max_gas_price is not None else 2 * price
    # Per voter: one relayed registration (~200k gas) + one relayed vote (~400k), each with a bounty.
    estimate = len(whitelist) * (600_000 * price + 2 * bounty)
    funding = w3.to_wei(a.fund, "ether") if a.fund is not None else estimate
    rcpt = deploy(
        w3, acct, artifact(name), sem, a.title, a.candidates, whitelist, reg_end, vote_start, vote_end, min_anon,
        bounty, max_gas_price, value=funding,
    )
    # Becomes the site's default election (the one shown without ?election= in the URL).
    cfg["election"] = rcpt.contractAddress
    write_config(cfg)
    export_contracts()
    print(f"{name} '{a.title}' at {rcpt.contractAddress} (block {rcpt.blockNumber})")
    print(f"  candidates {a.candidates}, whitelist {len(whitelist)}, minAnonymitySet {min_anon}")
    print(f"  relayer bounty {a.bounty}, refunds up to {max_gas_price / 1e9:g} gwei, funded with {funding / 1e18:g}"
          f" (estimate for full turnout: {estimate / 1e18:g})")
    print(f"  registration until +{a.reg}m, voting +{a.reg + a.gap}m .. +{a.reg + a.gap + a.vote}m (chain time)")
    print(f"open http://localhost:5173/?election={rcpt.contractAddress}")


if __name__ == "__main__":
    main()
