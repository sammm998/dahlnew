# SmartHangers – MVP

Systemet läser installatörens samordnade och låsta IFC-modell och tar fram följande ur samma modellfil:

1. **Offertunderlag**: kalkyl med besparing, känslighetsanalys och lönsamhetsgräns.
2. **Produktionsunderlag**: en SmartHanger per upphängning med måttsatt vy, BOM, kapoptimering och etiketter med QR-kod.
3. **Montageunderlag**: packlistor i montageordning och en mobil montageguide med referensmått och kvittens.
4. **Uppföljningsdata**: status per hanger, spårbarhetslogg, revisionshantering och IFC-export med hangers som objekt.

Det här är ett **beslutsstöd med mänsklig granskning**, inte ett autonomt konstruktionssystem. Varje genererad upphängning kan granskas, flyttas, läggas till eller tas bort innan något produceras. Offertgodkännande, konstruktionsgodkännande och produktionsfrisläppning är tre separata beslutspunkter.

> ⚠️ **Regelvärdena är exempel.** Regelbiblioteket som följer med (`backend/smarthangers/rules/default_rules.json`) innehåller platshållare för klamringsavstånd, vikter, svep, infästning, toleranser och liknande. Värdena **är inte verifierade** mot AMA, tillverkardata eller lastberäkning. Systemet låter inte konstruktionen godkännas förrän en tekniskt ansvarig har verifierat regelversionen i appen.

## Kom igång

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e "backend[dev]"
```

```bash
cd backend
python -m smarthangers.demo          # skapar ett demoprojekt med exempelmodell
python -m smarthangers --port 8000   # starta server
```

Öppna sedan http://localhost:8000. Välj roll uppe till höger: projektadministratör, BIM-/konstruktionsgranskare, sälj/kalkyl, produktionsoperatör, lager/pack, montör eller systemadministratör.

En egen testmodell skapar du så här (variant `b` är en reviderad R02 med flyttade, borttagna och nya rör):

```bash
python -m smarthangers.sample_ifc demo_R01.ifc
python -m smarthangers.sample_ifc demo_R02.ifc --variant b
```

Tester:

```bash
cd backend && python -m pytest
```

Data sparas i `backend/data/` (SQLite, uppladdade modeller och foton). Med miljövariabeln `SMARTHANGERS_DATA` styr du katalogen, med `DATABASE_URL` byter du databas (t.ex. `postgresql+psycopg://…`, kräver `pip install -e "backend[postgres]"`) och med `PUBLIC_BASE_URL` sätter du basadressen som QR-koderna pekar på.

## Flöde

| Steg | Var | Vad händer |
|---|---|---|
| 1. Import | Översikt → *Importera revision* | IFC läses med IfcOpenShell. Rör (`IfcPipeSegment`/`IfcFlowSegment`), rördelar, ventiler, väggar/balkar, bjälklag och grid extraheras. Varje uppladdning blir en ny revision (R01, R02 …). |
| 2. Höjder | Planvy (klicka på rör/stråk) och översikt | Rörcentrum läses ur modellen. Rörcentrum, underkant rör, takjärnshöjd per stråk eller per plan kan matas in manuellt. Varje höjd märks med källa: **modell**, **manuell** eller **beräknad**. |
| 3. Generering | *Generera upphängningar* | Rör delas upp i stråk, regelmotorn placerar upphängningar och varje SmartHanger får ett unikt ID (`P2-014`). |
| 4. Granskning | Planvy, Granskning | Flytta, lägg till eller ta bort hangers och sätt dem som granskad/godkänd. Hantera flaggade specialfall. |
| 5. Beslut | Översikt | Offert (sälj/kalkyl), konstruktion (granskare, kräver verifierade regler, godkända hangers och inga olösta kritiska flaggor) och produktionsfrisläppning. |
| 6. Produktion | Produktion | En hanger i taget med måttsatt vy och arbetsmoment. Operatören kvitterar och skriver ut etikett. Paus, materialbrist, avvikelse och omarbete stöds. |
| 7. Packning | Packning | Kollin planeras efter plan/zon, montageordning, vikt och antal. Packlistan visar uttagsordning. Status packad/levererad registreras per kolli. |
| 8. Montage | Montage (mobil) | Visar nästa hanger, referensmått mot gridlinje, UK takjärn och ett ritningsutsnitt. Montören kvitterar monterad, ej monterbar, avvikelse eller omarbete, med foto och kommentar. QR-koden på etiketten öppnar rätt hanger. |
| 9. Export | Översikt | Kompletterad IFC där varje hanger är ett `IfcDiscreteAccessory` med psetet `SH_SmartHanger` (ID, revision, status, position). BOM finns som CSV per projekt och per hanger. |

### Stråksegmentering och placering

Logiken finns i `segmentation.py`.

- Kolinjära rörsegment slås ihop till rörlinjer. Skarvarna blir spärrzoner.
- Parallella rörlinjer med begränsat inbördes avstånd och nivåskillnad grupperas till stråk som delar takjärn.
- Placeringskrav byggs upp från stråkändar, böjar, avstick/T-korsningar och punktlaster (ventiler, en upphängning inom X mm på var sida). Spärrzoner läggs runt väggenomföringar, hinder, rördelar och skarvar.
- Upphängningarna placeras så att alla krav uppfylls och max klamringsavstånd hålls för varje enskilt rör (det strängaste avståndet styr gemensamma takjärn).
- Avvikelser som inte kan lösas automatiskt flaggas.

**MVP-avgränsning (enligt specifikationen):** raka, horisontella, samordnade stråk hanteras automatiskt. Vertikala och lutande rör, nivåskillnader, kollisioner och T-korsningar flaggas för manuell hantering.

### Hangergeometri

Logiken finns i `generator.py`.

- **Takjärnets höjd** tas i första hand från manuellt värde för stråket, därefter manuellt värde för planet, därefter bjälklagets underkant i modellen. Saknas allt räknas den fram från rörens överkant plus ett standardavstånd, och då flaggas den.
- **Takjärnets längd och hålbild** ges av rörens inbördes läge plus kantmarginal, avrundat enligt regel.
- **Pendellängden** räknas individuellt per rör: UK takjärn − (rörcentrum + radie + isolering + svepets offset) + ingängning, avrundat.
- **Lasten** fördelas per pendel efter tributär längd, rörvikt inklusive isolering och punktlaster. Pendeldimensionen väljs efter last och säkerhetsfaktor. Takjärn och infästning kontrolleras mot sina kapaciteter.
- **Svep, infästning och ytbehandling** väljs efter dimension, underlag, brandkrav och korrosivitetsklass. Kända materialkonflikter varnas.

### Revisioner

När en ny modell laddas upp jämförs rören via GlobalId (nya, borttagna, flyttade, ändrade) och berörda hangers markeras.

- Hangers som redan är producerade, packade, levererade eller monterade och som påverkas flaggas som **kritiska ändringar**.
- Godkännanden återkallas.
- Vid omgenerering behåller oförändrade hangers sitt ID. Ändrade hangers får ny hanger-revision (A → B), borttagna blir *makulerade* och ID:n återanvänds aldrig.
- Manuellt justerade stråk behåller sina positioner.

### Regelmotor

Regelmotorn är versionshanterad och redigerbar: *Regler* i menyn, eller `GET/POST /api/rulesets`.

- **Klamringsavstånd:** material, dimension, horisontellt/vertikalt, modifierare för isolering och system.
- **Placering:** avstånd till ände, böj, avstick, punktlast, genomföring och skarv.
- **Last:** rörvikt, isolering, punktlast, kapacitet för pendel, takjärn och infästning.
- **Hangergeometri:** profil, marginaler, avrundning, max antal rör per takjärn.
- **Infästning:** underlag, brandkrav, kantavstånd, lastklass.
- **Miljö:** korrosivitetsklass → ytbehandling, materialkompatibilitet.
- **Toleranser.**
- **Artikelregister:** lagerlängder och vikter för kapoptimering.

Versionerna går igenom stegen utkast → publicerad (låst) → verifierad. Varje hanger lagrar vilken regelversion och vilka regelrader som styrt den.

## Arkitektur

| Område | Implementation |
|---|---|
| Backend/API | Python 3.10+, FastAPI (`backend/smarthangers/api.py`, OpenAPI på `/docs`) |
| BIM/geometri | IfcOpenShell: geometri via `ifcopenshell.geom`, centrumlinje med PCA (`ifc_import.py`) |
| Databas | SQLAlchemy, SQLite som standard och PostgreSQL via `DATABASE_URL` (`db.py`) |
| Frontend | Responsiv webbapp i vanilla JS med hash-routing, utan byggsteg (`static/`). Planvy och måttsatta vyer ritas som SVG. |
| Etiketter | PDF (ReportLab, 100×60 mm, QR) och ZPL II för Zebra (`labels.py`) |
| Export | IFC (`ifc_export.py`), CSV, PDF |

```
backend/smarthangers/
  ifc_import.py      IFC → rör, komponenter, grid, plan
  segmentation.py    stråk, krav, spärrzoner, placering
  generator.py       hangergeometri, last, BOM, kapoptimering, referensmått
  rules/             regelmotor + standardregler (exempel)
  services.py        projekt, revisioner, generering, granskning, status, packning
  calc.py            offert-/kalkylmodul
  labels.py          etiketter och packlistor
  ifc_export.py      kompletterad IFC
  api.py             REST-API och webbapp
  static/            frontend
  sample_ifc.py      syntetisk testmodell
tests/               enhetstester + end-to-end-flöde
```

## Kända begränsningar / innan skarp drift

- **Autentisering:** användare och roll skickas som headers (`X-User`, `X-Role`) och väljs fritt i gränssnittet. Det är bara för demo och test. Ersätt med riktig inloggning (OIDC/SSO) och rollkoppling innan systemet används på riktigt.
- **Regelvärden:** samtliga är platshållare och måste verifieras (se ovan).
- **Revit:** endast IFC läses. Revit-modeller exporteras till IFC.
- **3D-vy:** planvy och måttsatta sidovyer finns. Det finns ingen IFC-3D-viewer (web-ifc/Three.js är nästa steg).
- **Specialfall:** vertikala och lutande rör, nivåskillnader och kollisioner flaggas men konstrueras inte automatiskt.
- **Kalkylens standardvärden** (normtider, timkostnader, priser) är platshållare och redigeras per kalkyl.

## Kritiska beslut före utvecklingsstart (från specifikationen)

1. Vilka projekttyper, rörmaterial och dimensioner som ska ingå i MVP.
2. Tekniskt ansvarig för AMA, last, infästning och konstruktionsregler, som också verifierar regelversionen i systemet.
3. 3–5 anonymiserade IFC-filer med manuella facit, som ska ersätta den syntetiska testmodellen i testerna.
4. Referensmått och acceptabla toleranser (regelområdet `tolerance`).
5. Artikelregister, standardkomponenter, lagerlängder och etikettformat (`articles`, `labels.py`).
6. Gransknings- och godkännandeflöde (rollmatris i `auth.py`, statusövergångar i `services.py`).
7. Hantering av modelländringar efter offert och produktionsstart. Grundbeteendet är implementerat: kritisk flagga, återkallade godkännanden och hanger-revision.
