# BOT – zdrojový kód

Tento repozitár obsahuje publikovateľnú kópiu botov z `D:\BOT\NEW` k 21. 10. 2026. Obsahuje Python skripty, testy, HTML šablóny, spúšťacie BAT súbory a existujúce návody. Databázy, vstupné zoznamy, exporty, logy, priebežné stavy a skutočné prístupové údaje nie sú súčasťou repozitára.

## Príprava nového počítača

1. Nainštaluj Python 3.12+ a naklonuj repozitár.
2. V adresári vybraného bota nainštaluj závislosti z jeho `requirements.txt`, ak existuje. Pre samostatné odosielacie boty použi `pip install -r requirements-email-bots.txt` z koreňa repozitára. Pre Bazoš scraper navyše `pip install requests beautifulsoup4 tqdm`.
3. Doplň vlastné vstupné dáta do súborov, ktoré vybraný bot očakáva. Názvy súborov a príkazy sú v príslušnom README alebo na začiatku skriptu. Bez dát sa zber ani rozosielanie nedá reprodukovať.
4. Pre staršie SMTP boty nastav premenné prostredia `EMAIL_USER` a `EMAIL_PASS`. Testovacie Art4You skripty používajú aj `TO_EMAIL`. Produkčné Art4You skripty čítajú `GS_CREDENTIALS_FILE` (cesta k lokálnemu JSON kľúču služby Google). Hodnoty nikdy necommituj.
5. `NEW/SK_market/Webnaprenajom_SK/webnaprenajom_sending_bot_SK.py` používa lokálny `senders_config.json`. Vzor je v `senders_config.example.json`; skopíruj ho na `senders_config.json` a doplň vlastné údaje. V tomto adresári spusti aj `SPUSTIT_bot.bat`.

Niektoré pôvodné skripty majú pevne zadané názvy lokálnych CSV a šablón. Repozitár zachováva ich fungujúcu logiku, ale po klonovaní treba dodať príslušné vstupy a overiť konfiguráciu pred prvým odoslaním. Nepúšťaj produkčný odosielací režim iba na skúšku inštalácie.

## Kontrola bez odosielania

`python -m compileall -q NEW` overí syntax. Testy sú najmä v `NEW/SK_market/BEST_email_seo_cleaner_segentation_BOT/tests`, `NEW/PL_market/BEST_email_seo_cleaner_segentation_BOT/tests` a pri SK Webnaprenajom botovi. Tieto testy spúšťaj až po inštalácii závislostí príslušného bota.

Lokálne pôvodné súbory v `D:\BOT\NEW` neboli pri príprave repozitára upravené. Kód publikovaný tu číta heslá z prostredia a neobsahuje pôvodné vložené heslá.
