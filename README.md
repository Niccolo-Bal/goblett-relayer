# goblett relayer + local test election

Registrations and votes travel over **Nostr**. The site publishes each one as a Nostr
event tagged with the election's topic; the relayer subscribes to that topic and submits
them to the chain (`registerFor` / `vote`), paying the gas. The browser never talks to
the relayer directly, and anyone can run another relayer on the same topic.

| | |
|---|---|
| Config | `relayer/.env` for `RPC_URL`, `DEPLOYER_KEY`, `RELAYER_KEY` (see `.env.example`); not needed on local anvil |
| Relays | `nostrRelays` in `election.json` (default `wss://nos.lol`, `wss://relay.primal.net`; set with `setup_chain.py --nostr-relays`) |
| Topic | tag `["t", "goblett-<chainId>"]`, one per chain |
| Request, kind 9467 | content `{"type":"register", election, wallet, identityCommitment, signature}` or `{"type":"vote", election, proof, ranking?}` |
| Reply, kind 9468 | tag `["e", <request id>]`, content `{ok, txHash?, error?}` |

The relayer serves **any genuine goblett election** on its chain, so an election created
from the website works with no relayer configuration. Before forwarding, it checks that
the contract's code is exactly our `FPTPElection` / `RankedChoiceElection` build
(comparing runtime bytecode with the immutable slots zeroed) and that it uses our
Semaphore. It also refuses calls needing more than 3M gas. Without these checks anyone
could make the relayer pay for calls to arbitrary contracts.

Every event is signed with a fresh throwaway Nostr key. That signature authorizes
nothing: a registration is authorized by the wallet's EIP-712 signature and a vote by
its Semaphore proof, so a relayer can't change either; it can only ignore them. Replies
are a courtesy and unauthenticated; the site waits for the chain itself
(`hasRegistered` / `nullifierUsed`) before showing success.

## Run it locally (4 terminals)

```bash
# 1. chain (WSL)
~/.foundry/bin/anvil

# 2. one-time setup per chain start (Windows, in relayer/): deploys Semaphore, writes the
#    config, exports the contracts for the site, gives your wallet test funds
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt   # once
.venv\Scripts\python setup_chain.py --fund 0xYOUR_METAMASK

# 3. relayer (restart it after setup_chain.py deploys a new Semaphore, or after `forge build`)
.venv\Scripts\python relayer.py

# 4. site (in site/)
npm install && npm run dev        # open http://localhost:5173
```

Create an election from the site ("Create a new election": title, type, candidates,
voter wallets, registration end, voting end, relayer bounty and funding) and confirm the
deployment in MetaMask. The site then opens it at `?election=<address>`; share that link
with voters. `deploy_election.py` does the same from the command line.

After changing the contracts: `forge build`, then `setup_chain.py` again (it re-exports
`site/contracts.json`), then restart the relayer. Elections deployed from an older build
are refused by the relayer.

Skip waiting on a local chain: `.venv\Scripts\python advance.py voting 0xELECTION` (or
`ended`, or N seconds). This moves the chain's clock ahead of your computer's.

MetaMask: the site asks to switch to chain 31337 (adds it, RPC `http://127.0.0.1:8545`, if
missing). Voters only sign and need no funds; the wallet that creates an election pays
for its deployment. After restarting anvil, MetaMask may remember old transaction counts:
if a deployment hangs, use MetaMask's "Clear activity tab data" for the account.

`site/smoke-test.mjs [fptp|ranked]` runs the whole flow headlessly with anvil wallets.

## Relayer payment

Each election holds funding sent by its creator (with the deployment, or later by anyone).
Every relayed registration (`registerFor`) and every counted vote credits the relayer
inside the contract: its gas, at up to the election's `maxRefundGasPrice`, plus
`relayBounty`. Nothing is transferred during the relay itself.

```bash
.venv\Scripts\python withdraw.py 0xELECTION [0xELECTION ...]   # collect what is owed
```

Off the local chain the relayer refuses relays it would not be paid back for (election
out of funding, or its gas price cap below the current price); `REQUIRE_REIMBURSEMENT=0`
turns that off. After voting ends anyone can call `reclaim()` (button on the site) to
send the unused funding back to the creator.

## Arc mainnet (current deployment)

Chain 5042, RPC `https://rpc.mainnet.arc.io`, explorer `https://explorer.arc.io`, gas in
USDC. Semaphore is deployed at `0x21F9ee5fbA61be8755E49eE7Cb7F679077429482` (verifier
`0xf511442a8BaF021c56EF37E02aa391077A333c61`); both were checked byte for byte against
this repo's build. `relayer/election.json` and `site/public/election.json` point there.

- Relayer: put `RPC_URL=https://rpc.mainnet.arc.io` and a funded `RELAYER_KEY` in
  `relayer/.env`, then `python relayer.py`. It refuses relays an election won't pay back.
- Site: commit `site/public/election.json` and `site/contracts.json`; the host builds
  `site/` with `npm run build` and serves `dist/`.
- To regenerate the config without redeploying anything:
  `python setup_chain.py --rpc https://rpc.mainnet.arc.io --semaphore 0x21F9ee5fbA61be8755E49eE7Cb7F679077429482`
- Rehearse changes on a fork first: `anvil --fork-url https://rpc.mainnet.arc.io --port 8546`,
  a relayer with `RPC_URL=http://127.0.0.1:8546 ALLOW_DEV_KEYS=1`, and
  `RPC_URL=http://127.0.0.1:8546 node smoke-test.mjs` in `site/`.
- Real money: the relayer key holds real USDC in a plain file; keep its balance small and
  withdraw (`withdraw.py`) regularly.

## Testnet alongside mainnet (`/testnet`)

The same site build serves both networks: `/` reads `election.json` (mainnet) and
`/testnet` reads `election.testnet.json` (Arc testnet), showing a network badge.
`site/public/_redirects` makes the host serve the page under `/testnet`. Browser storage
and the Nostr topic both include the chain ID, so the two never mix.

The testnet side needs its own relayer process. Scripts run with `GOBLETT_NETWORK=testnet`
use `relayer/election.testnet.json` and read keys and RPC only from `relayer/.env.testnet`
(never from `.env`):

```bash
# relayer/.env.testnet: RPC_URL=https://rpc.testnet.arc.io, RELAYER_KEY=0x... (testnet funds)
set GOBLETT_NETWORK=testnet
.venv\Scripts\python relayer.py
```

`GOBLETT_NETWORK=testnet` works the same way for `setup_chain.py`, `deploy_election.py`
and `withdraw.py`.

## Deploying on Arc testnet

Arc testnet: chain 5042002, RPC `https://rpc.testnet.arc.io`, gas paid in USDC (18
decimals as the native token), explorer `https://testnet.arcscan.app`.

1. **Keys.** Create two fresh keys (`cast wallet new`), fund both from the Arc faucet, and
   put them in `relayer/.env` (copy `.env.example`; the file is gitignored). The scripts
   refuse to use the built-in anvil keys on a real chain, because those are public.
   Also fund the MetaMask wallet that will create elections.
2. **Rehearse on a fork** (free, optional): `anvil --fork-url https://rpc.testnet.arc.io
   --port 8546`, then run steps 3-5 with `RPC_URL=http://127.0.0.1:8546` and
   `ALLOW_DEV_KEYS=1`.
3. **Chain setup**, once: `.venv\Scripts\python setup_chain.py`. Deploys Semaphore
   (~12.5M gas in three transactions, roughly 0.3 USDC at 25 gwei) and writes the config,
   including the network details the site hands to MetaMask. To use a Semaphore you
   deployed and verified yourself (`forge script script/DeploySemaphore.s.sol`), pass
   `--semaphore 0x...`.
4. **Verify on Arcscan** so voters can check what they are trusting: Semaphore,
   SemaphoreVerifier and PoseidonT3 against npm `@semaphore-protocol/contracts` 4.14.5,
   and one election of each type (`forge verify-contract`).
5. **Relayer**: `.venv\Scripts\python relayer.py`, on a machine that stays on for the
   whole election.
6. **Site**: `npm run build` in `site/` and host `site/dist/` on any static HTTPS host.
   `election.json` (chain config) and the contract bytecode are part of the build, so
   build after step 3.

Things that differ from the local chain:

- No `advance.py` and no `--fund`: schedules run in real time and funds come from the faucet.
- The deployment transaction carries the whole whitelist. Arc's block gas limit is 30M and
  each wallet costs about 22k gas, so one election tops out around 1,200 wallets.
- Measured on a fork of Arc testnet at ~25 gwei: registration ~240k gas, FPTP vote ~340k,
  ranked vote ~390-460k. With a 0.0005 bounty each, budget about 0.015-0.02 USDC per voter.
- Arc rejects value transfers involving blocklisted addresses. A blocklisted relayer can
  still relay but can't withdraw; nothing else is affected.

## Limits

- The messages are public on the Nostr relays (as they are on a public chain), and the
  relays see the sender's IP and timing instead of our relayer. Voters' browsers also
  read the chain through the public RPC, whose operator sees their IP. Registering and
  voting from the same IP gives those operators a hint. Use Tor/VPN for anything real.
- Anyone can post to the topic. Invalid messages cost the relayer only a simulation
  (no gas), but there is no rate limit yet.
- A relay can still lose a little: the refund is an estimate (measured within about 5% of
  the real gas), and if two relayers submit the same message, the slower transaction
  fails and its gas is not refunded.
- The relayer catches up on at most the last 24 hours of events, and never on events
  older than the chain itself. After a local chain restart a new election can reuse an
  old address, and an old registration signature for it would otherwise still be valid.
- The identity secret is in the browser's localStorage only. Clearing site data loses
  the vote; back it up from the page.
