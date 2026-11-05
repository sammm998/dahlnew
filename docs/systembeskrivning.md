# SmartHangers – komplett systembeskrivning

_Konverterad från SmartHangers_komplett_systembeskrivning.docx._

# Bakgrund

Idag byggs rörupphängningar ute på byggarbetsplatsen. Montören behöver
mäta upp stråk, mängda material, kapa takjärn och gängstång, placera
svep, bygga upphängningar, märka upp och montera. Detta tar mycket tid,
kräver kvalificerad arbetskraft och innebär osäkerhet i projektekonomin.

Målet är att flytta detta arbete från byggarbetsplatsen till fabrik
genom prefabricerade upphängningar, SmartHangers.

# Syfte

Systemet ska automatiskt omvandla installatörens samordnade 3D-modell
till följande leveranser från samma modellfil:

1.  Offertunderlag

2.  Produktionsunderlag

3.  Montageunderlag

4.  Uppföljningsdata

# Grundidé

Installatören skickar en samordnad och låst 3D-modell från sitt projekt.
Systemet ska läsa modellen, identifiera rörstråk, beräkna upphängningar,
generera produktionsunderlag, montageguide och kalkyl. Resultatet blir
färdigbyggda upphängningar som levereras märkta och packade i
montageordning.

# Övergripande process

5.  Importera IFC-fil eller Revit-export.

6.  Identifiera rör och rörstråk.

7.  Läsa dimension, material, längd, riktning, position och
    systemtillhörighet.

8.  Läsa eller komplettera höjdinformation.

9.  Beräkna upphängningar enligt regelbibliotek.

10. Generera SmartHangers och tilldela varje objekt ett unikt ID.

11. Skapa produktionsdata, materiallista och etiketter.

12. Skapa packordning och montageguide.

13. Exportera kompletterad IFC och logga uppföljningsdata.

# Input

## Modellfil

-   IFC

-   Revit-export

-   Primärt fokus är IFC.

## Information som ska extraheras

-   Rördimension

-   Materialtyp

-   Rörlängd

-   Position och riktning

-   Parallella rör

-   Systemtillhörighet

-   Böjar, avstick, ventiler, genomföringar och nivåskillnader

# Höjdinformation

All höjdinformation finns inte alltid korrekt i modellen. Systemet ska
därför stödja både automatisk avläsning och manuell komplettering.

-   Automatisk läsning av exempelvis rörcentrum från modellen.

-   Manuell inmatning av rörcentrum, underkant rör eller önskad höjd på
    takjärn.

-   Beräkning av pendellängder, takjärnshöjd och sveppositioner.

-   Tydlig markering av om höjden kommer från modell, manuell inmatning
    eller beräkning.

# Regelmotor

Regelmotorn är systemets viktigaste komponent. Den ska använda
verifierade AMA-regler, klamringsregler, materialregler, lastregler och
upphängningsregler. Reglerna får inte hårdkodas utan ska vara
redigerbara och versionshanterade.

  -----------------------------------------------------------------------
  **Regelområde**                     **Exempel på parametrar**
  ----------------------------------- -----------------------------------
  Klamringsavstånd                    Material, dimension,
                                      horisontellt/vertikalt, isolering
                                      och system

  Placering                           Avstånd till böj, avstick, ventil,
                                      genomföring, skarv och stråkände

  Last                                Rörvikt, medium, isolering, antal
                                      rör och tillåten komponentlast

  Hangergeometri                      Takjärnslängd, antal pendlar,
                                      sveptyp och röravstånd

  Infästning                          Underlag, infästningstyp,
                                      kantavstånd, brandkrav och
                                      lastklass

  Miljö                               Korrosivitetsklass, ytbehandling
                                      och materialkompatibilitet

  Tolerans                            Kapmått, montageavvikelse och
                                      tillåtna positionsintervall
  -----------------------------------------------------------------------

Exakta regelvärden ska verifieras av tekniskt sakkunnig och får inte
byggas in enbart utifrån exempel eller antaganden.

# Stråksegmentering och specialfall

Systemet måste kunna avgöra när ett stråk börjar och slutar samt hur
upphängningsrytmen påverkas av följande:

-   Böjar

-   T-korsningar

-   Avstick

-   Ventiler och punktlaster

-   Genomföringar och schakt

-   Nivåskillnader

-   Kollisioner

-   Blandade rörmaterial och dimensioner

En första MVP kan begränsas till raka, horisontella och samordnade
rörstråk. Specialfall kan initialt flaggas för manuell hantering.

# SmartHanger-objekt

Systemet ska skapa ett separat dataobjekt för varje upphängning.

  -----------------------------------------------------------------------
  **Fält**                            **Innehåll/exempel**
  ----------------------------------- -----------------------------------
  Unikt ID                            P2-014

  Projekt och revision                Projekt X / modell R04 / hanger
                                      revision B

  Position                            X, Y, Z, plan och zon

  Referensmått                        Exempelvis 1 057 mm från vald vägg
                                      eller gridlinje

  Rör                                 Antal, dimension, material och
                                      individuell höjd

  Takjärn                             Typ, profil, kaplängd, hålbild och
                                      ytbehandling

  Pendlar                             Antal, dimension, kaplängd och
                                      position

  Svep/klammer                        Typ, dimension, antal och position

  BOM                                 Artikelnummer, kvantitet och
                                      kapmått

  Status                              Föreslagen, granskad, godkänd,
                                      producerad, packad, levererad,
                                      monterad

  Spårbarhet                          Ändringar, godkännare, användare
                                      och tidsstämplar
  -----------------------------------------------------------------------

# Granskning

Automatiskt genererade upphängningar ska alltid kunna granskas före
produktion. Behörig användare ska kunna flytta, ändra, lägga till eller
ta bort upphängningar. Offertgodkännande, konstruktionsgodkännande och
produktionsfrisläppning ska vara separata beslutspunkter.

# Produktionssystem

En fabriksoperatör ska kunna bygga varje upphängning utan att tolka
traditionella ritningar. En skärm ovanför arbetsbänken visar en
SmartHanger i taget.

14. Visa hanger-ID, projekt, revision och bild/3D-vy.

15. Kapa eller välj takjärn enligt profil och mått.

16. Kapa eller välj gängstänger/pendlar enligt individuella längder.

17. Placera muttrar, svep och övriga komponenter enligt måttsatt vy.

18. Utför egenkontroll av mått och komponentantal.

19. Markera klar och skriv ut etikett.

20. Visa nästa hanger enligt produktions- eller packordning.

-   Stöd paus, materialbrist, avvikelse och omarbete.

-   Stöd QR-/streckkod för projekt och hanger.

-   Generera BOM per hanger, kolli och projekt.

-   Stöd kapoptimering av takjärn och gängstång.

# Etiketter och QR-kod

-   Varje SmartHanger ska märkas med ID, projekt, plan/zon, revision,
    packsekvens och QR-kod.

-   QR-koden ska öppna rätt hanger i produktions- eller montagevyn.

-   Samma ID ska användas i modell, produktionsunderlag, etikett,
    packlista och montageguide.

# Materiallista (BOM)

Systemet ska skapa materiallista både per hanger och per projekt.

  -----------------------------------------------------------------------
  **Nivå**                            **Exempel**
  ----------------------------------- -----------------------------------
  Per hanger                          Takjärn 470 mm, 2 pendlar 800 mm, 2
                                      pendlar 1 200 mm, 4 DN54-svep, 8
                                      muttrar

  Per projekt                         Total mängd takjärn, gängstång,
                                      svep, muttrar och övriga
                                      komponenter
  -----------------------------------------------------------------------

# Packning och logistik

-   Gruppera hangers i kollin efter montageordning, område, vikt, volym
    och leveransetapp.

-   Packlistan ska visa vilka ID som ingår i varje kolli och i vilken
    ordning de ska tas ur.

-   Leveransstatus och avvikelser ska kunna följas per hanger och kolli.

# Montageguide

Montören ska nå guiden via webblänk eller framtida appintegration.

-   Visa nästa hanger och markerat ritningsutsnitt.

-   Visa ID, höjd, avstånd från vägg/grid och övriga
    montageinstruktioner.

-   Stöd sökning och QR-skanning.

-   Montören ska kunna markera monterad, ej möjlig att montera,
    avvikelse eller omarbete.

-   Logga datum, tid, användare, foto och kommentar.

-   Ändra färg/status på ritningen när hangern är monterad.

# IFC-export

-   Skapa ny eller kompletterande IFC med varje SmartHanger som objekt.

-   Lagra ID, position, revision och status som metadata.

-   Möjliggör användning i Dalux, Solibri, Navisworks och Revit-baserade
    arbetsflöden.

# Offert- och kalkylsystem

Kalkylsystemet ska visa kunden den ekonomiska nyttan och samtidigt ge
internt offertstöd.

## Indata

-   Antal upphängningar

-   Rördimensioner och rörmeter

-   Normtid VVS

-   Timkostnad montör och lärling

-   Materialkostnad

-   Uppstarts-/konstruktionskostnad

-   Styckpris, frakt och påslag

## Utdata

-   Traditionell material- och arbetskostnad

-   Kostnad med SmartHangers

-   Frigjorda montörstimmar

-   Kvarvarande lärlingstid

-   Ekonomisk besparing

-   Fastprisandel

-   Känslighetsanalys

-   Dynamisk lönsamhetsgräns

Alla antaganden ska vara synliga och redigerbara. Kalkylen ska märkas
som räkneexempel tills fast offert har lämnats.

# Modellrevisioner

-   Varje uppladdad modell ska bli en separat revision.

-   Systemet ska identifiera nya, borttagna, flyttade och ändrade rör
    samt berörda hangers.

-   Redan producerade eller monterade hangers som påverkas ska flaggas
    som kritiska ändringar.

-   Reviderade etiketter och produktionsinstruktioner ska kunna skapas.

-   Full ändringslogg ska sparas.

# Roller

  -----------------------------------------------------------------------
  **Roll**                            **Huvuduppgift**
  ----------------------------------- -----------------------------------
  Projektadministratör                Skapa projekt, importera modeller
                                      och hantera revisioner

  BIM-/konstruktionsgranskare         Granska geometri, regler och avsteg
                                      samt godkänna konstruktion

  Sälj/kalkyl                         Skapa kalkyl och offert

  Produktionsoperatör                 Följa instruktioner och rapportera
                                      avvikelser

  Lager/pack                          Plocka, packa, märka och registrera
                                      leverans

  Montör                              Följa montageguide och kvittera
                                      montage

  Systemadministratör                 Förvalta artikeldata, integrationer
                                      och inställningar
  -----------------------------------------------------------------------

# Teknisk arkitektur -- preliminärt förslag

  -----------------------------------------------------------------------
  **Område**                          **Förslag**
  ----------------------------------- -----------------------------------
  Backend/API                         Python och FastAPI

  BIM/geometri                        IfcOpenShell och kompletterande
                                      geometribibliotek

  Databas                             PostgreSQL; PostGIS kan utvärderas

  Frontend                            React/Next.js eller motsvarande

  3D-visning                          web-ifc/Three.js eller etablerad
                                      IFC-viewer

  Mobil                               Responsiv webbapp/PWA

  Etiketter                           ZPL eller PDF beroende på skrivare

  Integration                         REST API samt IFC/CSV/PDF i tidig
                                      fas
  -----------------------------------------------------------------------

# Rekommenderad MVP

  -----------------------------------------------------------------------
  **Fas**                             **Omfattning**
  ----------------------------------- -----------------------------------
  Fas 1                               IFC-import och visualisering av rör

  Fas 2                               Manuell höjdsättning och enkel
                                      regelmotor

  Fas 3                               Generera upphängningar och
                                      materiallista

  Fas 4                               Produktionsvy, etiketter och
                                      kvalitetskvittering

  Fas 5                               Packning och mobil montageguide

  Fas 6                               IFC-export, kalkylmodul och
                                      revisionshantering
  -----------------------------------------------------------------------

# Kritiska beslut före utvecklingsstart

21. Bestäm vilka projekttyper, rörmaterial och dimensioner som ska ingå
    i MVP.

22. Utse tekniskt ansvarig för AMA, last, infästning och
    konstruktionsregler.

23. Säkra 3--5 anonymiserade IFC-filer och manuella facit.

24. Definiera referensmått och acceptabla toleranser.

25. Bestäm artikelregister, standardkomponenter, lagerlängder och
    etikettformat.

26. Definiera gransknings- och godkännandeflöde.

27. Bestäm hur modelländringar efter offert och produktionsstart ska
    hanteras.

# Viktig slutsats

Projektets kärna är inte webbgränssnittet, etiketterna eller
kalkylatorn. Den verkliga tekniska utmaningen och affärstillgången är
att på ett kontrollerat och spårbart sätt översätta IFC-modell och
verifierade regler till korrekta SmartHanger-objekt, produktionsunderlag
och montageunderlag.

Första versionen ska vara ett beslutsstöd med mänsklig granskning, inte
ett helt autonomt konstruktionssystem. Regelbibliotek och
projektresultat måste kvalitetssäkras inom ett fastställt tekniskt
ansvar.
