# Instagram native farm

Play Store Instagram is optional. Default: **one unique Nomix clone per phone.** One account per phone. One Drive folder per phone.

## Šta pokrećeš

1. `start_proxy_pool.bat`
2. `start_dashboard.bat` → **http://127.0.0.1:3001**
3. Content Bay → mapa serial → Drive folder
4. Run Control → format ili **E2E pack**

## Drive (po telefonu)

Svaki telefon = jedan Drive folder, unutra:

```
<phone folder>/
  Reels/      → reel
  Posts/      → feed
  Stories/    → story
  captions.txt  (opciono)
```

Dva načina mapiranja:

1. **JSON** `ig_phone_drive_map.json` — `{ "SERIAL": "FOLDER_ID" }` (primer: `ig_phone_drive_map.example.json`), ili Console Content Bay.
2. **Parent folder** `ig_phones_drive_folder.txt` — child folderi se zovu pun serial ili last-8.

Share svaki folder (ili parent) sa `service_account.json` client_email.

Claim ledger: `used_ig_accounts/phone_{serial}_{fmt}.txt` — telefoni **ne** dele isti pool.

## Setup

```bat
cd C:\farm\instagram-native
pip install -r requirements.txt
copy Instagram_farm_accounts.csv.example Instagram_farm_accounts.csv
copy floppydata_proxy.json.example floppydata_proxy.json
copy ig_phone_drive_map.example.json ig_phone_drive_map.json
```

CSV `model` kolona se ignoriše (sve je `ig`). Stari tiana/diana/karly redovi i dalje rade.

## E2E

```bat
python -u run_ig_farm.py --e2e --story-link https://example.com --highlight Highlights --allow-unproven
```
