# Pravidlá bota (záväzné)

## 1. Hlavička From (incident 2026-10-14)
- `From` sa skladá **len** cez `email.utils.formataddr((meno, adresa), charset="utf-8")`. Nikdy `f"{meno} <{adresa}>"`.
  Meno s diakritikou (Maroš, Matúš) sa inak zakóduje spolu s adresou, relay v `From` nevidí adresu a prepíše ju na
  `"..."@smtp.hostcreators.sk` -> DMARC fail (hostcreators.sk `p=reject`) + antispam zamietnutia (Websupport, Gmail, Microsoft).
- Vynucuje to `validate_outgoing_message()`: kontrola pred každým odoslaním a preflight pri štarte `send_loop`.
  Pri chybe sa nepošle nič (`HEADER_INVALID`).
- Po akejkoľvek zmene skladania správ: najprv testovací email z oboch účtov (menu -> "Chceš odoslať testovací email?"),
  skontrolovať `From` v prijatej správe, až potom ostré posielanie.

## 2. Bounce-y a fronta opakovania (menu 5)
- Po každom behu (a aspoň denne) spustiť menu **5 – Spracovať bounce-y**. Páruje DSN s odoslanou správou cez Message-ID
  a do logu dopíše riadok (log je append-only, platí posledný stav kontaktu):
  - `INVALID` – adresa/doména neexistuje (5.1.x, 5.4.x, "no such user"); nikdy znova,
  - `BOUNCED` – vrátené kvôli poškodenej hlavičke From; čaká vo fronte opakovania,
  - `REJECTED` – zamietnuté pri správnej hlavičke (spam, plná schránka...); automaticky sa neopakuje, len eviduje.
- Fronta opakovania sa pošle **na začiatku ďalšieho ostrého behu daného účtu, pred novými riadkami**, podľa rovnakých
  pravidiel ako bežné posielanie (jeden email na doménu, doménový zámok, potlačené adresy, pacing, limity/hod).
  Opakovanie nikdy neposúva kurzor CSV. Doména doručená inou adresou sa neopakuje. Po úspešnom opakovaní (`OK`) je kontakt uzavretý.
- Pri zamietnutí so správnou hlavičkou sa automaticky neopakuje (zabráni cyklu); vyhodnotí sa ručne.

## 3. Všeobecné
- Jeden email na doménu; jedna doména = jeden odosielateľ (`domain_owner.json`). Matúš vždy `template_matus_ai.html`.
- Nikdy sa nepripájať priamo na cudzie MX servery (sondovanie poškodilo reputáciu IP; neexistujúca adresa sa zisťuje
  z bounce-ov a z reálneho pokusu cez `smtp.hostcreators.sk`).
- Logy/stav neupravovať počas behu bota. Pred ručným zásahom záloha. Beží vždy len jedna inštancia (`bot_running.lock`).
- Súbory: logy `send_log_wnp.csv` (Maroš), `send_log_matus_ai.csv` (Matúš); spoločný kurzor `bot_state_wnp_parallel.json`.
- Stavy v logu: `OK`, `FAILED` (nejasné/technické, kontakt sa neopakuje automaticky), `INVALID`, `BOUNCED`, `REJECTED`.

## 4. Čo robí ktorá voľba v menu
- **1 / 2 / 3 (posielanie):** po výbere odosielateľa sa vypíše prehľad s frontou nedoručených. Po potvrdení sa najprv pošle fronta
  (BOUNCED), potom sa pokračuje od pozície v `bot_state_wnp_parallel.json`. Kontakt/doména s posledným stavom
  OK/INVALID/FAILED/REJECTED sa nikdy nepošle znova (kontrola cez všetky logy oboch účtov, aj po reštarte).
- **4 (report):** len výpis, nič sa nemení ani neodosiela.
- **5 (bounce-y):** nič sa neodosiela. Berú sa len vrátené správy odoslané týmto botom (párovanie cez Message-ID z logu);
  cudzia pošta v schránke sa ignoruje. Pred zápisom do logu sa spýta a urobí zálohu.
