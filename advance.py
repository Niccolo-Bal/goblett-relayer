"""Local anvil only: jump chain time to the next phase of the configured election.

    python advance.py voting     # to votingStart
    python advance.py ended      # to votingEnd
    python advance.py 300        # forward N seconds
    python advance.py voting 0xElection   # a specific election (default: the one in election.json)
"""

import os
import sys

from common import LOCAL_CHAIN_ID, artifact, connect, load_config


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    cfg = load_config()
    w3 = connect()
    if w3.eth.chain_id != LOCAL_CHAIN_ID and os.environ.get("ALLOW_DEV_KEYS") != "1":
        raise SystemExit("advance.py only works on a local anvil chain; a real chain's clock can't be moved")
    addr = w3.to_checksum_address(sys.argv[2] if len(sys.argv) == 3 else cfg["election"])
    el = w3.eth.contract(address=addr, abi=artifact("Election")["abi"])
    now = w3.eth.get_block("latest").timestamp
    target = {
        "frozen": lambda: el.functions.registrationEnd().call(),
        "voting": lambda: el.functions.votingStart().call(),
        "ended": lambda: el.functions.votingEnd().call(),
    }.get(sys.argv[1], lambda: now + int(sys.argv[1]))()
    if target > now:
        w3.provider.make_request("evm_setNextBlockTimestamp", [target])
    w3.provider.make_request("evm_mine", [])
    print(f"phase is now: {el.functions.phase().call()}")


if __name__ == "__main__":
    main()
