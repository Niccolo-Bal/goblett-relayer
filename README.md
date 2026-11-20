
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
 


## Arc
 
| | Mainnet | Testnet |
|---|---|---|
| Chain ID | 5042 | 5042002 |
| RPC | `https://rpc.mainnet.arc.io` | `https://rpc.testnet.arc.io` |
| Explorer | `https://explorer.arc.io` | `https://testnet.arcscan.app` |
| Config | `election.json` | `election.testnet.json` |

Gas is paid in USDC. Mainnet Semaphore is at `0x21F9ee5fbA61be8755E49eE7Cb7F679077429482` (verifier `0xf511442a8BaF021c56EF37E02aa391077A333c61`), checked byte for byte against this repo's build.
 

