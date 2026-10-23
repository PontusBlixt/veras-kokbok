# Ändringslogg

## v0.6.0 – 2026-10-23
- Appen går att **installera** (PWA): manifest, ikoner (skål med ånga) och service worker. Genvägar till *Laga* och *Ingredienser* när man håller inne ikonen
- Fungerar på Android, iPhone (Lägg till på hemskärmen) och dator, via Tailscale-adressen (HTTPS)
- Når appen inte servern visas "Kan inte nå kokboken" i stället för förhandsvisningsläget, och den laddar om sig själv när servern svarar igen
- Recept, skafferi och bilder sparas aldrig i service workern, utan hämtas alltid från servern

## v0.5.0 – 2026-10-23
- Ny flik **Laga** (`#laga`): visar vilka recept som går att laga med det som finns hemma
  - **Kan lagas nu** – alla vitala ingredienser finns hemma
  - **Saknar en sak** / **Saknar fler** – med det som saknas i orange
  - **Okänt** – recept vars ingredienser inte finns i skafferiet än
  - Filtrera på huvudkategori
- Receptens ingrediensrader matchas mot skafferiet på namn, med enkel hantering av böjningar ("gula lökar" = "Gul lök", "finhackad gul lök" = "Gul lök")
- Ingredienser som saknas i skafferiet kan läggas till direkt från Laga
- Receptsidan visar vad som saknas hemma och märker raderna
- Tillbaka-knappen på receptsidan går till fliken man kom ifrån

## v0.4.0 – 2026-10-21
- Ny flik **Ingredienser** (skafferi): varje ingrediens läggs in en gång som **Vital** (antal med +/−, 0 = Slut) eller **Statisk** (finns alltid)
- Filter Alla / Hemma / Slut, sök, "Spara + ny" för att lägga in många i rad
- Startmall: 6 statiska (salt, peppar, oljor, mjöl, socker) och 5 vitala (ägg, mjölk, smör, gul lök, vitlök). Allt går att ta bort
- +/− räknas på servern så att två telefoner inte skriver över varandra
- Förslag: skafferiet föreslår namn från recepten, och receptformuläret föreslår namn från skafferiet
- Versionsvarning om `server.py` och `static/index.html` inte matchar

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
