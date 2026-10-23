# Veras Kokbok

Liten kokboksapp: Python 3-server (inga paket att installera) + SQLite + ett webbgränssnitt.
Körs på hemmaservern i `~/veras-kokbok` och nås via Tailscale.

## Innehåll
- `server.py` – servern och API:t
- `static/index.html` – appen (ska ligga i mappen `static/`, inte i roten)
- `veras-kokbok.service` – systemd-tjänst (mall, `ANVANDARE` byts vid installation)
- `uppdatera.sh` – säkerhetskopierar databasen, hämtar senaste versionen och startar om
- `CHANGELOG.md` – vad som ändrats i varje version
- `data/` – databasen (`kokbok.db`), bilderna (`images/`) och säkerhetskopior. Skapas av servern och ligger **aldrig** i git.

## Förhandsvisning
Öppnas `static/index.html` utan servern körs appen i **förhandsvisningsläge**: en blå FÖRHANDSVISNING-markering visas och datan sparas bara i webbläsaren. Klicka på markeringen för att återställa.

## Första installationen (görs en gång)

### 1. Lägg upp koden på GitHub
Skapa ett tomt, privat repo `veras-kokbok` på GitHub (ingen README, ingen .gitignore). Sedan på servern:
```bash
cd ~ && unzip veras-kokbok.zip      # zip-filen innehåller redan git-historiken
cd ~/veras-kokbok
git remote add origin git@github.com:PontusBlixt/veras-kokbok.git
git push -u origin main --tags
```

### 2. Starta tjänsten
```bash
sudo ss -tlnp | grep 8090           # tomt svar = porten är ledig
sed "s/ANVANDARE/$USER/g" ~/veras-kokbok/veras-kokbok.service | sudo tee /etc/systemd/system/veras-kokbok.service
sudo systemctl daemon-reload
sudo systemctl enable --now veras-kokbok
curl -s http://127.0.0.1:8090/api/health   # ska svara {"ok": true, ...}
```

### 3. Lägg ut den på Tailscale
```bash
tailscale serve status                                       # vilka portar används redan?
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8090
tailscale serve status                                       # kokboken ska nu synas här
```
Öppna `https://<servernamn>.<ditt-tailnet>.ts.net:8443` från datorn och från Veras telefon (båda med Tailscale, i samma tailnet). Inställningen ligger kvar efter omstart. Används 8443 redan, välj en annan port (t.ex. 10000).

Får du fel om certifikat: slå på **MagicDNS** och **HTTPS Certificates** under DNS i Tailscales adminkonsol.

## Nå den direkt på hemnätet (utan Tailscale)
Appen kan också lyssna på serverns vanliga nätverkskort, så att datorer hemma når den på `http://server.home:8443` utan Tailscale.

```bash
ip -4 -br addr                          # leta upp serverns LAN-adress, t.ex. 192.168.50.121 (inte 100.x = Tailscale)
getent hosts server.home                # ska ge samma adress

sudo mkdir -p /etc/systemd/system/veras-kokbok.service.d
printf '[Service]\nEnvironment=KOKBOK_LAN=192.168.50.121:8443\n' | sudo tee /etc/systemd/system/veras-kokbok.service.d/lan.conf
sudo systemctl daemon-reload && sudo systemctl restart veras-kokbok
journalctl -u veras-kokbok -n 5 --no-pager   # ska visa "Lyssnar även på http://192.168.50.121:8443"
```
- Byt `192.168.50.121` mot din adress. Bind till LAN-adressen, inte `0.0.0.0`, annars krockar den med Tailscales 8443.
- Det är `http://`, inte `https://`. HTTPS finns bara via Tailscale-adressen.
- Ge servern en fast adress (DHCP-reservation i routern). Byter den adress försöker appen igen var 30:e sekund och skriver det i loggen.
- Kör du `ufw`: `sudo ufw allow from 192.168.50.0/24 to any port 8443 proto tcp`.
- Appen har ingen inloggning. Alla på hemnätet (även gäster på wifi) kan se och ändra recept.

## Uppdatera till en ny version
```bash
cd ~/veras-kokbok && ./uppdatera.sh
```
Tar en säkerhetskopia av databasen (de 20 senaste sparas i `data/backup/`), kör `git pull`, startar om och visar vilken version som körs.

Gå tillbaka till en tidigare version:
```bash
git checkout v0.2.0 && sudo systemctl restart veras-kokbok    # tillbaka: git checkout main
```

## Manuell säkerhetskopia
`uppdatera.sh` kopierar bara databasen. Bilderna ligger som vanliga filer i `data/images/` och ändras bara när någon lägger till eller tar bort en bild.
```bash
python3 -c "import sqlite3; s=sqlite3.connect('$HOME/veras-kokbok/data/kokbok.db'); d=sqlite3.connect('$HOME/kokbok-kopia.db'); s.backup(d)"
tar czf ~/kokbok-bilder-$(date +%F).tgz -C ~/veras-kokbok/data images
```

## Så funkar appen
- **+** (nere till höger): välj Huvudkategori, Underkategori eller Recept.
- **Redigera**: visar knappar på varje rad – lägg till i kategorin, byt namn/flytta, ta bort.
- **Sök**: söker i alla nivåer, även på sökvägen ("huvud fisk"). Klick/Enter hoppar dit.
- **Övrigt**: kan inte tas bort. Recept från borttagna kategorier flyttas hit.
- **Receptsidan**: klicka på ett recept. Bild överst, ingredienser med mått och beskrivningen. Varje recept har en egen länk (`#recept-12`).
- **Ingredienser**: en rad per ingrediens med mängd, enhet och ingrediens. Klistra in en hel lista i första fältet så delas den upp.
- **Fliken Ingredienser** (skafferiet, `#ingredienser`): varje ingrediens läggs in en gång och är antingen
  - **Vital** – har ett antal. Tryck **+** när du handlat och **−** när den tar slut. 0 = *Slut*.
  - **Statisk** – finns alltid hemma (salt, olja …), inget antal.
  Filtrera på Alla / Hemma / Slut. Tryck på namnet för att byta namn, byta typ eller ta bort.
- **Versionsvarning**: har `server.py` och `static/index.html` olika version visas en gul ruta överst i appen.
