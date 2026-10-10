> Aktueller Stand: **285 Tests bestanden**, nur der echte Arena-Test gesperrt.
> MCP als separater Prozess mit Neustart, Parallelität und externen Testdaten geprüft;
> Datengrenzen korrigiert. Details: `MCP_DEEP_REVIEW.txt`. Frühere Stände folgen unten.

> Neueste Nachprüfung: **271 Tests bestanden, nur der gesperrte Arena-Test übersprungen**.
> MCP installiert und geprüft; unklare Indexklauseln und widersprüchliche Belege abgesichert.
> Details: `SKIPPED_TESTS_FOLLOWUP.txt`. Ältere Messstände unten bleiben historische Befunde.

> Neuer Prüfstand: **250 Tests bestanden**, erneut 600 Störvarianten bestanden.
> Unbekannte Fälle, Fehlerwiederaufnahme und paralleles lokales HTTP zusätzlich geprüft.
> Details und verbleibende Grenzen: `LIVE_READINESS_REVIEW.txt`.

> Aktueller Stand nach erweitertem Modellvergleich: **GPT-6 Luna**, 109 bestandene
> reguläre Tests, 600 bestandene Störfälle und 2.960 lokale Verhandlungen über
> 1.480 Szenarien. Der vollständige aktuelle Bericht steht in `MODEL_EVALUATION.txt`.
> Die unten dokumentierten früheren Modellstände und Messreihen bleiben historische Befunde.

# Professionelle Verhandlungspolitik und vertiefte Bewertung

Stand: 10. Oktober 2026. Diese Ergänzung beschreibt den implementierten Agenten,
die zusätzlichen Tests und die verbleibenden Grenzen. Sie ersetzt kein echtes
Ergebnis im Tacto-Finale. Die Startanleitung steht in `EVIDENCE_BUYER.md`.

## Was der Agent erreichen soll

Der Agent vertritt den Einkauf. Er versucht, ein wirtschaftlich gutes und
verlässliches Gesamtpaket zu erreichen. Der niedrigste Stückpreis ist dafür
nicht allein entscheidend: Zahlungsziel, Fracht, Lieferzeit, Qualität, gebundene
Menge, Vertragsdauer und gegebenenfalls Lieferantenstabilität verändern den Wert.

Ein Wunschpreis ist ein Ziel; eine Preisobergrenze ist eine verbindliche Grenze.
Diese Unterscheidung ist jetzt im Code abgebildet. Der Agent darf sein Ziel im
Verlauf innerhalb seiner Grenzen anpassen. Eine Obergrenze darf er auch bei
Modellfehler, Zeitdruck oder einem attraktiven Begleittext nicht überschreiten.
Es werden keine beliebigen Euro-Grenzen für alle Teile vorgegeben.

Ein Abbruch kann die richtige Entscheidung sein. Ein Abschluss oberhalb der
zulässigen Kosten oder mit gebrochenen Pflichtbedingungen ist kein Erfolg.
"Immer gewinnen" ist weder eine wissenschaftlich begründete Strategie noch eine
prüfbare Garantie gegenüber unbekannten Verhandlungspartnern.

## Freundlich, verbindlich, geschäftlich

Der Agent formuliert respektvoll und präzise. Er würdigt Angebote, fragt nach
betrieblichen Prioritäten und erklärt, dass Pakete Alternativen sind. Er erfindet
keine persönliche Beziehung, gemeinsamen Interessen oder Konkurrenzangebote.
Verbindliche Konditionen stehen in den vollständigen Angebotsblättern.

Beispiele aus dem tatsächlich verwendeten Formulierungsbestand:

- Einstieg: “Thank you for your proposal. We would like to agree commercially sound terms for both businesses.”
- Zahlungsinteresse: “Would earlier payment help make the overall package more workable for you?”
- Lieferfähigkeit: “Which delivery schedule can your operation reliably commit to?”
- Fortsetzung: “Thank you for the update. We have reviewed the commercial package.”
- Entscheidung vorbereiten: “Please confirm the complete package you can support so we can reach a clear decision.”

Humor ist optional, selten und an ein positives Signal gebunden. Der Satz
“Ideally, the next thing we simplify is the paperwork.” kommt höchstens beim
zweiten eigenen Beitrag infrage, nach einer positiv formulierten Antwort.
Bei Qualitäts- und Kapazitätsfällen sowie erkannten Beschwerden, Schäden,
Verzögerungen, Dringlichkeit oder Unzufriedenheit wird er weggelassen.
Eine einfache Schlagworterkennung kann Ironie oder verdeckten Ärger übersehen;
für besonders sensible Anwendungen lässt sich `light_humor = false` setzen.

Das Sprachmodell wählt passende Gesprächsbausteine; es darf keine freien
Preiszusagen, behaupteten Fakten oder Abschlussaktionen erzeugen. Das ist bewusst
kontrolliert, kann sprachlich aber weniger abwechslungsreich sein als ein völlig
freier Gesprächsagent. Die aktuelle Standardsprache ist Englisch, passend zum Kit.

## Vertiefte Forschung und Konsequenzen

Die ursprüngliche Recherche zu mehreren wirtschaftlich vergleichbaren Angeboten,
Perspektivübernahme, Beziehungspflege und Humor bleibt in `EVIDENCE_BUYER.md`
mit Originalquellen dokumentiert. Ergänzend wurden folgende Befunde geprüft:

**Aggressive Anker haben einen Preis.** Petrowsky et al. (2025) berichten eine
präregistrierte Metaanalyse mit 90 Studien, 374 Effekten und 16.334 Teilnehmenden.
Ein erstes Angebot hatte im Mittel einen Vorteil (`g = 0,42`); ambitionierte
Angebote verbesserten den Abschlusswert (`g = 1,14`), führten aber auch zu weniger
Abschlüssen (`g = −0,42`) und schlechterem subjektivem Erleben der Gegenseite
(`g = −0,40`). Zwei zusätzliche Experimente umfassten 2.121 Personen. Diese
standardisierten Effekte sind keine Prozentpunkte für unseren Agenten. Tacto
beginnt zudem regelmäßig mit einem Lieferantenangebot, sodass Erstangebotsbefunde
nicht unmittelbar auf unsere Gegenangebote übertragbar sind.
[Originalarbeit](https://www.martinschweinsberg.com/content/files/2025/12/Petrowsky-et-al-2025-OBHDP-1.pdf).

**Zugeständnisse sollten nachvollziehbar und bedingt sein.** Deepak Malhotras
Beitrag beim Harvard Program on Negotiation empfiehlt unter anderem, Zugeständnisse
kenntlich zu machen und an Gegenleistungen zu knüpfen. Das ist hier eine fachliche
Praxisempfehlung, keine numerische Erfolgsprognose. Vollständige alternative Pakete
koppeln beispielsweise Zahlung und Preis. Der einmalige Nachbesserungsversuch bei
einer unerfüllten Pflichtbedingung bleibt innerhalb der geprüften Grenzen.
[Originalbeitrag](https://www.pon.harvard.edu/daily/negotiation-skills-daily/four-strategies-for-making-concessions/).

**Übertragung auf die Implementierung:** Keine beliebig extremen Gegenangebote,
kein automatisches Nachgeben bis zum Abschluss, getrennte Ziele und Grenzen,
mehrere sachlich unterscheidbare Pakete, kontrollierte Schlussentscheidung.
Die konkreten Anker von 12% beziehungsweise 6%, der Zeitverlauf der Zugeständnisse
und die begrenzte 3%-Kompensation für ein finales Reparaturpaket sind weiterhin
technische Heuristiken. Keine der Quellen beweist diese Werte als optimal.

## Unsichere Lieferantenkosten

Für strategische Fälle werden vorhandene Should-cost-Daten zunächst auf ein Stück
normalisiert. Eine Angabe pro 100 oder 1.000 Stück darf nicht als Stückpreis in die
Rechnung eingehen. Das im Datenpaket beschriebene Fehlerband wird als Band behandelt,
nicht als statistische Wahrscheinlichkeit oder Sicherheit eines Vorhersagemodells.

Die Umsetzung kombiniert zwei Kostengrenzen mit zwei angenommenen Finanzierungssätzen.
Der ungünstigste berechnete Gesamtkostenwert einschließlich Beziehungskosten bestimmt
die Bewertung. Bei der Angebotssuche werden auch mehrere Preise betrachtet, weil ein
niedrigerer Preis bei strategischen Lieferanten eine höhere Risikobelastung auslösen kann.

Diese Bewertungsregel gilt beim Agenten und im übergeordneten Ablauf für reguläre
Abschlüsse, Modellfehler und Zeitablauf. Harte Preis- und Kostenlimits bleiben gültig.
Wenn eine belastbare Schätzung fehlt oder die Gewichtung mehrerer Teile unbekannt ist,
wird diese Lücke im Zustand vermerkt. Der Agent behauptet dann keine bekannte Marge.

Die Szenariowerte sind Sensitivitätsannahmen. Verdeckte Finanzierungskosten,
Kapazitätseffekte oder Fehler in den Quelldaten können damit nicht vollständig
abgedeckt werden. Eine echte weltweite oder mathematische Optimalitätsgarantie wäre falsch.

## Was zusätzlich getestet wurde

88 automatisierte Prüfungen wurden erfolgreich ausgeführt; zwei zusätzliche
Live-Tests wurden absichtlich nicht ausgeführt. Neben diesem Testsatz wurden 60 Entwicklungsszenarien
und anschließend 120 neue Kombinationen gegen fünf selbst programmierte Gegnertypen
ausgeführt: kooperativ, hart, liquiditätsorientiert, unbeweglich und manipulativ.
Jede Kombination wurde mit dem bisherigen Basisagenten und der neuen Variante
getestet, insgesamt 360 lokale Verhandlungen. Es wurden keine Arena-Versuche benutzt.

Variiert wurden Teilefälle, Ausgangspreise, Rundenzahl, Pflichtbedingungen,
strategische Kostenannahmen und teilweise absichtlich unerreichbare Preisgrenzen.
Die Reservierungslogik der Testgegner befindet sich ausschließlich im Simulator;
der Agent bekommt sie nicht in seine Beobachtungen übermittelt.

| Ergebnis | Entwicklung: 60 Fälle | Neue Kombinationen: 120 Fälle |
|---|---:|---:|
| Vom Prüfer gefundene erfüllbare Schlusskonditionen | 42 | 79 |
| Abschlüsse Basisagent | 23 | 48 |
| Abschlüsse neuer Agent | 39 | 73 |
| Verpasste nachweislich erfüllbare Fälle, neuer Agent | 3 | 6 |
| Verletzte geprüfte Grenzen, neuer Agent | 0 | 0 |
| Durchschnittliche Kostenänderung bei beiderseitigem Abschluss | −0,73% | +0,14% |

Die neue Variante erzielt mehr regelkonforme Abschlüsse. Sie erzielt aber nicht
bei jedem gemeinsamen Abschluss den niedrigeren Preis. Die Kostenmittelwerte
beziehen sich nur auf Fälle, die beide Agenten abschließen, und sind daher kein
Gesamtnutzenvergleich über alle Fälle. Für Abbrüche wird keine erfundene Kostenersparnis
angesetzt. Die synthetische Fallverteilung ist keine Stichprobe echter Unternehmen.

Der Vergleich bewahrt die ursprüngliche Angebotslogik des Basisagenten. Bei beiden
werden verbindliche Abschlüsse an denselben zusätzlichen Grenzen geprüft. Beim
Basisagenten notwendige sichere Abbruchreaktionen sind deshalb nicht pauschal
Programmabstürze; sie spiegeln teilweise fehlende Berücksichtigung der neuen Vorgaben.

Das zusätzliche Prüfprogramm sucht nach erfüllbaren Paketen am letzten bekannten
Preisniveau der simulierten Gegenseite. Ein gefundener Treffer beweist eine
verpasste Chance. Kein Treffer beweist bei unsicheren Kosten nicht zwingend, dass
es weltweit kein besseres Paket gibt. Die 120 neuen Fälle kombinieren dieselben
fünf Gegnerfamilien neu; sie ersetzen keinen Test gegen die echten Arena-Bots.

## Gefundene und behobene Probleme

- Abweichende Berechnung bei normaler Entscheidung und Notfallabschluss: vereinheitlicht.
- Nicht durchgehend erzwungene Preisgrenzen: Prüfung auch bei Vorschlägen und Agentenausfall.
- Zu frühes Aufgeben bei erfüllbaren Pflichtbedingungen: einmaliger kontrollierter Reparaturversuch.
- Zu viele wiederholte Einstiegsformulierungen: kontextabhängige Fortsetzungen.
- Humor ohne positives Gegenübersignal: enger begrenzt und in kritischen Situationen unterdrückt.
- Möglicher Präzisionsverlust bei Indexzahlen: Wiedergabe der ursprünglichen Zahl statt verkürzter Formatierung.
- Vermischung Wunschpreis und verbindliches Limit: getrennte, fallbezogene Parameter.
- Fehlende Kennzeichnung von Schätzlücken: expliziter Eintrag im Agentenzustand.

## Einsatzbereitschaft

Die lokale Implementierung und die OpenAI-Anbindung sind getestet. Der API-Schlüssel
bleibt in der ignorierten lokalen Datei. Die Praxisversuche der Arena bleiben unberührt.
Nicht ausgeführte Live-Tests sind keine ignorierten Fehlermeldungen: Sie würden genau
die vom Nutzer ausgeschlossenen Versuche verbrauchen.

Vor einem echten Finale müssen die offizielle Sonntags-Ergänzung, der richtige
Arena-Teamzugang, die ausgewählte finale Konfiguration und der eingefrorene Commit
zusammenpassen. Ein überprüfter Softwarestand ersetzt diese veranstaltungsbezogenen
Schritte nicht. Die finale Einreichung und Live-Finalverhandlungen wurden nicht ausgelöst.

Die Final-Konfiguration ist separat vorbereitet: höchstens 500 Modellaufrufe mit
512 Ausgabetokens pro Aufruf; dies ist kein Euro-Kostenlimit. Lokale KI-Tests
bleiben auf zwölf Aufrufe begrenzt. Kein Final-Lauf wurde gestartet.


## Ergänzung: neue Fälle, neue Daten und Kosten (10.10.2026)

Der Agent ist nicht auf die drei lokalen Fallnummern oder vorhandene Teilnummern
beschränkt. Er übernimmt die Fallbeschreibung, erlaubte Konditionen, Pflichtbedingungen,
Standardkonditionen und verfügbaren Belege des jeweiligen Falls. Neue Teile und
Lieferanten ohne Historie erhalten keine erfundenen Eigenschaften. Ein neuer Fall mit
festem Preis, verhandelbaren Zahlungszielen und einer zusätzlichen Pflichtkondition
wurde separat geprüft – auch mit strategischer Kostenbewertung. Neue Felder können
über die vorgegebenen Optionen und Pflichtbedingungen berücksichtigt werden; neue
Kostenformeln oder ein geändertes API-Schema erfordern weiterhin eine Codeanpassung.
Der Agent lernt nicht selbstständig beliebige neue Regeln und verändert seinen Code
im Finale nicht.

Die Sonntags-Ergänzung kann im Datenpaket, als benachbarter Ordner `release-sunday`
oder über `ARENA_RELEASE` bereitgestellt werden. Alle drei Wege wurden getestet.
Doppelte Fakten-IDs werden abgewiesen. Neue Dokumente werden vom richtigen Paket
aufgelöst. Auch externe Ergänzungsordner fließen jetzt in die Datenprüfsumme ein;
Änderungen verhindern das Fortsetzen eines eingefrorenen Laufs. Neue Dateien deshalb
vor dem finalen Start einspielen und mit `ready` prüfen. Neue Fallwellen werden vom
Runner automatisch abgefragt; neue lokale Dateien werden nicht während eines
laufenden Finales unkontrolliert nachgeladen.

### Modell und Kosten

Im damaligen Stand war `gpt-4.1-mini-2025-04-14` konfiguriert. Die Auswahl wurde mit
unserer konkreten kleinen Aufgabe getestet: situationsgerechte Gesprächsbausteine
wählen. Preise und Annahmeentscheidungen bleiben in der geprüften Berechnung.
Ein größeres Modell ist dafür bislang nicht durch einen gemessenen Vorteil begründet.
Es ist eine günstige, getestete Wahl; ein Vergleich aller verfügbaren Modelle wurde
nicht durchgeführt und ein universell bestes Modell wird nicht behauptet.

Offizielle Standardpreise: 0,40 USD pro Million Eingabetokens und 1,60 USD pro Million
Ausgabetokens. Quelle, geprüft 10.10.2026:
https://developers.openai.com/api/docs/models/gpt-4.1-mini

Der neue lokale KI-Test verwendete 24.616 Eingabe- und 243 Ausgabetokens. Ohne
Cache-Rabatt gerechnet ergibt das **0,0102352 USD** für zwölf Aufrufe und drei lokale
Verhandlungen. Dies ist eine Preisschätzung aus gemeldeter Nutzung, keine Rechnung.

Technisch erzwungen werden pro finalem Lauf höchstens 500 Modellaufrufe, maximal
16.384 Bytes serialisierte Eingabe und 512 Ausgabetokens je Aufruf. Lokale KI-Läufe
bleiben auf zwölf Aufrufe begrenzt. Überlange Eingaben werden vor dem Netzaufruf
abgewiesen. Parallele Aufrufe können das gespeicherte Aufruflimit nicht umgehen;
beim Fortsetzen desselben Laufs bleibt das Budget erhalten. Unklare Timeouts zählen
weiter als verbrauchte Aufrufe und werden nicht automatisch wiederholt. Bei einem
KI-Fehler oder erschöpftem Budget verhandelt der Agent mit sicheren Standardtexten weiter.

Grobe konservative Rechenreserve bei 500 maximalen Texteingaben: etwa 4 USD bei
heutigen Preisen, einem Token je UTF-8-Byte plus zusätzlicher Nachrichtenreserve.
Das ist ausdrücklich kein erzwungenes Dollar-Limit und keine Guthabengarantie.
Ein neuer separater Lauf erhält ein neues Aufrufbudget. Andere Anwendungen im selben
OpenAI-Projekt können zusätzlich Kosten verursachen. Kontoguthaben und EHL-Gutschrift
sind nicht verifiziert. Der gemessene kleine Test darf nicht als garantierter Preis
für unbekannte finale Fälle hochgerechnet werden.

### Wettbewerbsforschung, sinnvoll übertragen

Die BOA-Forschung trennt Angebotsstrategie, Gegnerverständnis und Annahmeregeln.
Eine darauf aufbauende Arbeit gewann ANAC 2013 gegen 19 teilnehmende Teams; das ist
historische Evidenz für diese Architektur, keine Leistungszusage für Tacto.
Quelle: https://research.tue.nl/en/publications/what-to-bid-and-when-to-stop/

Renting, Hoos und Jonker (AAMAS 2020) berichten in ihrem ANAC-ähnlichen Versuchsaufbau
5,1 Prozent höheren Verhandlungsnutzen gegenüber dem zweitbesten Agenten durch
konfigurierte Strategien. Zugleich betonen sie, dass keine feste Strategie in jeder
Situation die beste ist. Daraus folgt hier: getrennte Kostengrenzen und Sprachsteuerung,
mehrere Gegenübertypen testen und neue Fälle prüfen. Wir übernehmen nicht die 5,1
Prozent als erwarteten eigenen Vorteil und haben keine fremden privaten Team-Repos
als Vergleichsbasis verwendet.
Quelle: https://research.tudelft.nl/en/publications/automated-configuration-of-negotiation-strategies/

### Erneute Prüfung und verbleibender Startbedarf

- Vollständige lokale Testsuite: **95 bestanden, 2 Live-Tests bewusst ausgelassen**.
- Erneut 120 synthetische Szenarien für beide Agenten: 73 gegenüber 48 Abschlüssen,
  keine Verletzung geprüfter Abschlussgrenzen; gemeinsame Abschlüsse im Mittel +0,14 % Kosten.
- Neue Regressionstests: unbekannte Teile/Lieferanten, zusätzliche Pflichtkondition,
  fester Preis, strategischer Festpreisfall, drei Ergänzungsordner, Dokumentauflösung,
  Faktenkollisionen, veränderte Ergänzungsdaten und Eingabegrenze vor Netzaufruf.
- Neuer lokaler KI-Lauf `20261010T154403Z-e465ca16`: drei abgeschlossene Testfälle,
  zwölf erfolgreiche Modellaufrufe, keine unklare Nutzung, lokaler Score 0,756003.
- Statische Prüfungen bestanden. Kein gehosteter Arena-Versuch gestartet.

`run_evidence.py ready` ist eine neue reine Offline-Startprüfung. Sie zeigt nur die
Anwesenheit von Zugangsdaten, niemals Schlüsselwerte. Der aktuelle Befund: OpenAI
vorhanden, 10.356 Basisfakten verfügbar; Arena-Schlüssel und Sonntags-Ergänzung fehlen.
Eine vorhandene Zeichenfolge beweist dabei noch keine gültige Arena-Berechtigung.

Vor dem echten Start verbleiben: offizielles Ergänzungspaket laden, Arena-Teamzugang
bereitstellen und read-only prüfen, finalen Commit nach dem Datenupdate einfrieren,
Jury-Zugriff/Einreichung samt Pitch-Unterlagen sicherstellen und den Runner rechtzeitig
auf einem stabil laufenden Rechner starten. Der Agent braucht keinen öffentlichen
Webserver. Die Live-Verbindung, Teamabgabe und ein Sieg sind noch nicht nachgewiesen.
