"""Collect what elections owe this relayer (gas refunds + bounties).

    python withdraw.py 0xElection [0xElection ...]
    python withdraw.py --min 0.05 0xElection ...   # only collect amounts of at least 0.05
    python withdraw.py --force 0xElection ...      # collect whatever is there

A withdrawal is itself a transaction (~50k gas, about 0.00125 USDC on Arc at 25 gwei) and
nobody refunds it. That is more than two bounties, so withdrawing after every relay loses
money. By default this script only withdraws when the amount is at least 20 times the
withdrawal's own cost (so the fee is under 5%); smaller amounts stay in the election until
they have grown. Nothing is lost by waiting: what is owed can be collected at any time,
also after the election has ended.

Uses RELAYER_KEY and RPC_URL (environment or relayer/.env).
"""

import sys

from web3 import Web3

from common import account, artifact, connect, expected_gas_price, revert_reason, send


WITHDRAW_GAS = 50_000  # measured: 49,881


def main():
    args = sys.argv[1:]
    force = "--force" in args
    minimum = None
    if "--min" in args:
        i = args.index("--min")
        try:
            minimum = Web3.to_wei(args[i + 1], "ether")
        except (IndexError, ValueError):
            raise SystemExit("--min needs an amount, e.g. --min 0.05")
        del args[i : i + 2]
    addresses = [a for a in args if a != "--force"]
    if not addresses:
        raise SystemExit(__doc__)

    w3 = connect()
    relayer = account(w3, "RELAYER_KEY")
    abi = artifact("Election")["abi"]
    fee = WITHDRAW_GAS * expected_gas_price(w3)
    threshold = 0 if force else (minimum if minimum is not None else 20 * fee)
    for addr in addresses:
        el = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=abi)
        owed = el.functions.owed(relayer.address).call()
        if owed == 0:
            print(f"{addr}: nothing owed")
            continue
        if owed < threshold:
            print(
                f"{addr}: {owed / 1e18:.6f} owed, left in place (withdrawing costs ~{fee / 1e18:.6f};"
                f" waiting for at least {threshold / 1e18:.6f}, or use --force)"
            )
            continue
        try:
            rcpt = send(w3, relayer, el.functions.withdraw())
            paid = rcpt.gasUsed * rcpt.effectiveGasPrice
            print(f"{addr}: withdrew {owed / 1e18:.6f}, fee {paid / 1e18:.6f} (tx {rcpt.transactionHash.to_0x_hex()})")
        except Exception as e:
            print(f"{addr}: withdraw failed: {revert_reason(e)}")


if __name__ == "__main__":
    main()
