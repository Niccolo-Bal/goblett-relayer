"""Collect what elections owe this relayer (gas refunds + bounties).

    python withdraw.py 0xElection [0xElection ...]

Uses RELAYER_KEY and RPC_URL (environment or relayer/.env).
"""

import sys

from web3 import Web3

from common import account, artifact, connect, revert_reason, send


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    w3 = connect()
    relayer = account(w3, "RELAYER_KEY")
    abi = artifact("Election")["abi"]
    for addr in sys.argv[1:]:
        el = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=abi)
        owed = el.functions.owed(relayer.address).call()
        if owed == 0:
            print(f"{addr}: nothing owed")
            continue
        try:
            rcpt = send(w3, relayer, el.functions.withdraw())
            print(f"{addr}: withdrew {owed / 1e18:.6f} (tx {rcpt.transactionHash.to_0x_hex()})")
        except Exception as e:
            print(f"{addr}: withdraw failed: {revert_reason(e)}")


if __name__ == "__main__":
    main()
