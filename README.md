
# Goblett relayer
 
Voters sign registrations and votes in the browser and publish them to Nostr. The relayer picks them up and submits them to the chain, paying the gas. Anyone can run their own relayer on the same topic.
 
- Topic: `["t", "goblett-<chainId>"]`
- Request (kind 9467): `{"type":"register", election, wallet, identityCommitment, signature}` or `{"type":"vote", election, proof, ranking?}`
- Reply (kind 9468): `{ok, txHash?, error?}`, tagged with the request's event id
- Relays: `nostrRelays` in `election.json` (defaults: `wss://nos.lol`, `wss://relay.primal.net`)
 
The relayer serves any goblett election on its chain. It only forwards calls to contracts  that use our Semaphore, and it refuses anything over 3M gas.
 
Nostr events are signed with throwaway keys and authorize nothing. A registration is authorized by the wallet's EIP-712 signature and a vote by its Semaphore proof, so a relayer can drop a request but can't change it. Replies can't be trusted, so the site checks the chain before showing success.
 
## Run locally
 
```bash
# 1. chain (WSL)
~/.foundry/bin/anvil
 
# 2. setup (Windows, in relayer/): deploys Semaphore, writes config, funds your wallet
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python setup_chain.py --fund 0xYOUR_METAMASK
 
# 3. relayer (restart after setup_chain.py or forge build)
.venv\Scripts\python relayer.py
 
# 4. site (in site/)
npm install && npm run dev        # http://localhost:5173
```
 
Create an election on the site and confirm the deployment in MetaMask. Share the `?election=<address>` link with voters. `deploy_election.py` does the same from the command line.
 
Config lives in `relayer/.env` (see `.env.example`); it isn't needed on local anvil. The scripts read compiled contracts from `contracts/<Name>.json`, copied from the goblett repo's `dapp/out/` Foundry build. They must match the build the elections were deployed from. After changing contracts, run `forge build`, copy the artifacts, rerun `setup_chain.py` and restart the relayer.
 

## Payment
 
Each election holds funding from its creator. Every relayed registration or counted vote credits the relayer with the gas it cost (up to `maxRefundGasPrice`) plus `relayBounty`.
 
Withdrawing is its own transaction (~50k gas) and isn't refunded, so it costs more than two bounties. The relayer withdraws once per election after voting ends, if the amount owed beats the gas cost. Set `AUTO_WITHDRAW=0` to turn that off, or collect by hand:
 
```bash
.venv\Scripts\python withdraw.py 0xELECTION [0xELECTION ...]
```
 
Off the local chain, the relayer skips relays it wouldn't be repaid for (set `REQUIRE_REIMBURSEMENT=0` to change that). After voting ends, anyone can call `reclaim()` to return unused funding to the creator.
 
## Arc
 
| | Mainnet | Testnet |
|---|---|---|
| Chain ID | 5042 | 5042002 |
| RPC | `https://rpc.mainnet.arc.io` | `https://rpc.testnet.arc.io` |
| Explorer | `https://explorer.arc.io` | `https://testnet.arcscan.app` |
| Config | `election.json` | `election.testnet.json` |

Gas is paid in USDC. Mainnet Semaphore is at `0x21F9ee5fbA61be8755E49eE7Cb7F679077429482` (verifier `0xf511442a8BaF021c56EF37E02aa391077A333c61`), checked byte for byte against this repo's build.
 
**Deploy a network:**
 
1. Make keys with `cast wallet new` and put them in `relayer/.env`. The scripts refuse the public anvil keys on a real chain. On testnet, fund them from the faucet.
2. Run `python setup_chain.py` to deploy Semaphore and write the config. If Semaphore is already deployed, pass `--semaphore 0x...`.
3. Run `python relayer.py` on a machine that stays on for the whole election.
4. Run `npm run build` in `site/` and host `dist/` anywhere that serves static HTTPS. Build after step 2.
5. Verify the contracts on the explorer so voters can check them.
 
Testnet scripts use `GOBLETT_NETWORK=testnet`, which reads `relayer/election.testnet.json` and `relayer/.env.testnet`. To run both networks with one wallet, put `RELAYER_KEY=0x...` in `relayer/relayer.env` and run:
 
```bash
.venv\Scripts\python relayer.py mainnet testnet
```
 
A key in `.env` or `.env.testnet` overrides the shared one. The wallet holds real USDC on mainnet. To try changes first, fork the chain with `anvil --fork-url <rpc> --port 8546` and run the relayer with `RPC_URL=http://127.0.0.1:8546 ALLOW_DEV_KEYS=1`.
 
Arc notes:
 
- An election tops out around 1,200 wallets, because the whole whitelist goes in the deployment transaction (30M block gas limit).
- Budget about 0.015-0.02 USDC per voter at 25 gwei and a 0.0005 bounty.
- Arc rejects transfers involving blocklisted addresses. A blocklisted relayer can still relay but can't withdraw.
 
## Limits
 
- Messages are public. Nostr relays see the sender's IP and timing, and so does the RPC operator. Registering and voting from the same IP gives them a hint, so use Tor or a VPN for anything real.
- Anyone can post to the topic. Bad messages cost a simulation, not gas, but there's no rate limit yet.
- The relayer only catches up on the last 24 hours of events, and never on events older than the chain.
- The identity secret is stored only in the browser's localStorage. Clearing site data loses the vote, so back it up from the page.
 
