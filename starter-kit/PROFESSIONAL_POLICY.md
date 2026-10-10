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
