# Paralelné odosielanie – 12. 10. 2026

Spusti `SPUSTIT_bot.bat`, vyber režim a odosielateľov `3` (obaja).
Šablóny zostávajú pôvodné: Maroš podľa režimu, Matúš používa svoju AI šablónu.

Oba účty bežia vo vlastných vláknach a pripravujú správy súčasne.
SMTP spojenie sa otvára a prihlasuje až po čakaní na časový slot.
Ukladanie do Sent cez IMAP nasleduje po úspešnom SMTP odoslaní a môže
prebiehať súčasne s prípravou druhého účtu.
Jeden spoločný koordinátor prideľuje ďalšie vhodné riadky striedavo Marošovi
a Matúšovi. Medzi SMTP odoslaniami oboch účtov je aspoň 8 sekúnd.
Pri 190/h na každý účet je spoločný rozostup najmenej 9,47 sekundy;
na jednom účte najmenej 18,95 sekundy. Sieťové operácie môžu tempo spomaliť.
Zostáva limit 190 správ v 59-minútovom okne na každý účet.

Pri prvom spustení sa pokračuje z Marošovho existujúceho `start_index`,
nie z Matúšovho nízkeho indexu. Pri audite bol `start_index` už **65981**:
riadok 65980 bol úspešne odoslaný a bol zapísaný v logu.
Ak by bol `start_index` 65980, Maroš dostane 65980, Matúš 65981,
Maroš 65982 a Matúš 65983. Duplicity, chýbajúce kontakty, blokované adresy
a riadky mimo aktívnych kampaní sa preskakujú. Existujúca rezervácia domény
môže pri obnovení určiť, ktorý účet ju dokončí.

Používa sa iba jeden vybraný kontakt na doménu. Domény aj adresy sa kontrolujú
naprieč históriou oboch účtov a režimov vrátane neúspešných pokusov.
Názvy domén sa porovnávajú bez rozdielu veľkosti písmen a prefixu `www.`.
Zostávajú existujúce filtre kontaktov a pravidlá výberu primárneho emailu.

Spoločný progres režimu Web na prenájom je `bot_state_wnp_parallel.json`.
Vytvorí sa pri prvom prideľovaní po potvrdení ostrého posielania.
`start_index` označuje najnižší nedokončený riadok, `next_index` ďalší riadok
na prideľovanie a `pending` rozpracované rezervácie. Pri ďalšom spustení je
tento súbor autoritatívny aj pri výbere jedného účtu. Individuálne JSON súbory
a CSV logy sa naďalej zapisujú po každom pokuse. Režim kampaní má samostatný
spoločný súbor `bot_state_parallel.json`.

Zápisy progresu sú atomické. Pri prerušení cez Ctrl+C sa zastavia oba účty
a počká sa na ukončenie rozpracovaných operácií. Nedokončené rezervácie
ostávajú uložené. Ak má rezervácia fázu `sending`, ale výsledok chýba v logu,
obnovenie sa zablokuje: treba najprv skontrolovať Sent príslušného účtu.
SMTP a lokálny JSON nemajú spoločnú transakciu, preto pri takom páde nemožno
bez kontroly bezpečne určiť, či server správu prijal. Pri strate spojenia
po začatí SMTP sa správa automaticky neopakuje.

Bot povoľuje iba jeden proces. Po páde odstráň `bot_running.lock` až po
overení, že pôvodný proces už nebeží. Neprepisuj progres na 65980 a nemaž
spoločný progres počas behu. Pri výbere jedného účtu tento účet prevezme
neodoslané rezervácie druhého účtu vo fáze `reserved`. Rezervácie vo fáze
`sending` bez výsledku v logu sa automaticky neprevádzajú.

Overenie: `unittest`, celé interaktívne CLI s izolovaným CSV/JSON/logmi,
dve skutočné vlákna a lokálne SMTP/IMAP náhrady vrátane reálneho merania
8-sekundového odstupu. Testy neodosielajú emaily. Webové rozhranie projekt
nemá, preto browser overenie nie je použiteľné.

V tejto relácii bol použitý pribalený Python a schválené lokálne závislosti:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) '.test-deps'
& 'C:\Users\Lenov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest -v
```

Zálohy pôvodného skriptu a testov majú príponu `.bak_parallel_20261012`.
Pre návrat zastav bot a obnov tieto dva súbory. Spoločný progres ponechaj
na samostatnú kontrolu; stará verzia ho nepozná. Produkčné JSON, CSV databáza,
konfigurácia účtov a existujúce odosielacie logy neboli pri úprave menené.

## Oprava nečinného SMTP spojenia

Po spustení používateľ doložil úspešný Matúšov riadok 65982 a následné
chyby `4.4.2 smtp.hostcreators.sk Error: timeout exceeded` na 65984,
65986 a 65988. Hosty, porty a limity oboch účtov sa zhodujú.
Pôvodná paralelná úprava otvárala SMTP spojenie pred čakaním na slot.
Matúšovo spojenie tak mohlo čakať takmer celý interval jedného účtu
nečinné. Lokálny regresný test s 20-sekundovým čakaním a SMTP serverom
s 15-sekundovým idle limitom túto chybu reprodukoval; presný idle limit
Hostcreators nebol overený.

Oprava otvára SMTP až v pridelenom slote. IMAP sa pripája až na uloženie
úspešne odoslanej správy. Chyby obsahujú fázu, typ výnimky a SMTP kód,
napríklad `smtp_send: SMTPSenderRefused SMTP 421: ...`.
Dočasné odmietnutie kontaktu kódom 4xx sa označí FAILED, nie INVALID.
Rozostupy a ochrana pred opakovaním nejasných odoslaní zostávajú zachované.

Záloha verzie pred touto opravou je
`webnaprenajom_sending_bot_SK.py.bak_smtp_idle_20261012`.
Oprava bola overená bez pripájania ku skutočným účtom a bez odosielania
emailov. Existujúce FAILED záznamy zostali zachované a automaticky sa
neopakujú. Pri audite tejto opravy bol spoločný `start_index` už 65989;
aktuálnu pozíciu pri ďalšom spustení určuje spoločný JSON.

## Pokračovanie iba z jedného účtu

Po zastavení oboch účtov môžu v spoločnom JSON zostať pripravené riadky
vo fáze `reserved`. Pri výbere iba Matúša alebo iba Maroša vybraný účet
prevezme aj tieto neodoslané riadky. Prevzatie sa vykoná až pri pridelení
riadku po potvrdení ostrého posielania; vlastníctvo domény sa prepíše
atomicky pod zámkom a rezervácia sa uloží s novým účtom.

Previesť možno iba rezerváciu vo fáze `reserved`, ktorá nemá žiadny pokus
v histórii domén alebo kontaktov. Fáza `sending` bez záznamu v logu naďalej
vyžaduje kontrolu Sent. Zmena vlastníka domény počas obnovovania zastaví
odosielanie. Dokončené domény a kontakty sa neodosielajú znova.

Pri audite tohto prípadu čakali riadky 66123 (Maroš) a 66124 (Matúš), oba
vo fáze `reserved`. Po výbere iba Matúša pokračuje Matúš od spoločnej pozície
66123. Produkčné JSON a logy sa pri oprave kódu nemenili.
Regresné testy overujú prevzatie oboma smermi, zablokovanie nejasného SMTP,
zachovanie už odoslaných domén a celý CLI postup pri výbere iba Matúša.
Záloha skriptu pred opravou má príponu `.bak_single_resume_20261012`.
