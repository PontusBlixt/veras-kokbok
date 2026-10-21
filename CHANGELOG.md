# Ändringslogg

## v0.3.1 – 2026-10-21
- Kan lyssna på fler adresser via `KOKBOK_LAN`, t.ex. `192.168.1.20:8443`, så att appen nås på hemnätet (`http://server.home:8443`) utan Tailscale
- Försöker igen var 30:e sekund om LAN-adressen inte finns än, i stället för att stoppa appen
- systemd-tjänsten väntar på att nätverket är uppe (`network-online.target`)

## v0.3.0 – 2026-10-21
- Egen receptsida: bild överst, ingredienser med mått, brödtext för tillagningen
- Ingredienser är rader med mängd, enhet och ingrediens. Klistra in en hel lista så delas den upp automatiskt
- Bilder: välj i formuläret eller direkt på receptsidan. Skalas ner i webbläsaren, sparas i `data/images/`
- Direktlänk till recept (`#recept-12`), webbläsarens bakåtknapp fungerar
- Befintliga recept migreras automatiskt från fritext till rader vid första start

## v0.2.0 – 2026-10-21
- Förhandsvisningsläge: öppnas `static/index.html` utan server körs appen i webbläsaren med en blå FÖRHANDSVISNING-markering
- Nytt utseende (grönt tema) med stöd för ljust och mörkt läge
- Installeras i `~/veras-kokbok`, uppdateras med `./uppdatera.sh`

## v0.1.0 – 2026-10-21
- Fliken Kategorier: utfällbart träd med huvudkategorier, underkategorier (valfritt djup) och recept
- Sök i alla nivåer, hoppar direkt till träffen
- Lägg till / byt namn / flytta / ta bort via + och Redigera
- Startmall med kategorier som går att ta bort
- Övrigt: kan inte tas bort, recept från borttagna kategorier flyttas dit
