# Jeden projekt Supabase dla trzech sklepów

Sam katalog z dokumentu `Prompt_Claude_Shop_Backends.md` możesz zaimportować po
wykonaniu migracji poleceniem:

```powershell
.\.venv\Scripts\python.exe scripts/import_products_supabase.py --env-file .env
```

Skrypt dodaje lub aktualizuje 24 produkty i 6 kategorii w każdym sklepie oraz
konfigurację sklepu wymaganą przez relacje walut. Nie tworzy klientów, kluczy ani
metod dostawy. Nie usuwa zamówień ani produktów spoza katalogu i nie odtwarza
stocku istniejących SKU. Zaktualizowane ceny i pochodzenie mogą unieważnić wyceny.
`--shop shop-pl` ogranicza import do jednego sklepu; `--dry-run` wyświetla JSON
bez połączenia z bazą. Dane syntetyczne znajdują się w `app/seed/data.py`.

Backend używa FastAPI, SQLAlchemy i psycopg, łącząc się bezpośrednio z PostgreSQL.
W jednym projekcie są trzy oddzielne zestawy tabel: `shop_pl.products`,
`shop_de.products`, `shop_ru.products` oraz analogiczne tabele klientów, koszyków,
zamówień i pozostałych danych. Schemat jest przestrzenią nazw tabel; tabele są
fizycznie osobne. Każdy sklep ma własnego użytkownika bazy z ograniczonymi
uprawnieniami. `SHOP_ID` ustala sklep przy uruchomieniu procesu.

## Konfiguracja w PowerShell

1. Utwórz jeden projekt Supabase. W panelu **Connect** skopiuj URI PostgreSQL
   dla **Session pooler** (port 5432, działa z IPv4) lub **Direct connection**
   (wymaga dostępnego IPv6 albo dodatku IPv4). Do migracji nie używaj transaction
   poolera na porcie 6543.
2. W `.env` wpisz `MIGRATION_DATABASE_URL` z hasłem bazy projektu, np.
   `postgresql://postgres.PROJECT_REF:HASLO_URL_ENCODED@HOST_Z_PANELU:5432/postgres?sslmode=require`.
   Użyj dokładnego hosta z panelu. Znaki specjalne w haśle zakoduj jako procentowe
   kodowanie URL. To hasło PostgreSQL, nie klucz API Supabase.
3. Zainstaluj zależności i wygeneruj konfigurację sklepów:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m app.cli --env-file .env gen-credentials
   ```

   Generator zachowa istniejące hasła i klucze demonstracyjne. Adresy
   `DATABASE_URL_SHOP_PL`, `DATABASE_URL_SHOP_DE`, `DATABASE_URL_SHOP_RU` zostaną
   wyprowadzone z jednego `MIGRATION_DATABASE_URL`, zachowując host, port, bazę
   i parametry TLS. Dla shared poolera automatycznie dopisze `.PROJECT_REF` do
   nazw użytkowników sklepów. `DATABASE_URL` zostanie ustawiony dla `SHOP_ID`.
   Plik zawiera sekrety i jest ignorowany przez Git.
4. Utwórz schematy i role, wykonaj migracje i załaduj dane demonstracyjne:

   ```powershell
   .\.venv\Scripts\python.exe -m app.cli --env-file .env bootstrap
   ```

   Operacja używa konta właściciela i tworzy wszystkie trzy zestawy tabel.
   Seed jest powtarzalny i nie odtwarza sprzedanych stanów magazynowych.
   Przy kolejnych zmianach używaj polecenia `migrate` zamiast `bootstrap`,
   jeśli nie potrzebujesz danych demonstracyjnych.
5. W ustawieniach Data API nie dodawaj `shop_pl`, `shop_de`, `shop_ru` do
   **Exposed schemas**. Backend nie potrzebuje `SUPABASE_URL`, klucza `anon`
   ani `service_role`. Tabele możesz przeglądać wybierając schemat w Table Editor.
6. Uruchom wszystkie warianty:

   ```powershell
   .\.venv\Scripts\python.exe scripts/run_shops.py --env-file .env
   ```

   Sklepy są dostępne na `http://127.0.0.1:8001/docs`,
   `http://127.0.0.1:8002/docs`, `http://127.0.0.1:8003/docs`.
   Sprawdź `/health/ready` na każdym porcie. Endpoint MCP to `/mcp`.
   Chronione operacje wymagają klucza danego sklepu z `DEMO_API_KEY_SHOP_*`.
   Launcher sprawdza wspólny endpoint i identyfikator projektu, a każdy proces
   dostaje tylko własny adres połączenia. Nie zmieniaj ręcznie tych adresów
   na połączenia do innych projektów.

Domyślne narzędzia nadal obsługują `.env.local`; powyższe komendy jawnie
wybierają `.env`. Dla produkcji ustaw `APP_ENV=production`, zmniejsz pule
(`DB_POOL_SIZE=2`, `DB_MAX_OVERFLOW=0`, jeśli limit połączeń jest mały)
i przechowuj konfigurację administracyjną poza procesami obsługującymi ruch.
`sslmode=require` wymusza TLS; `verify-full` z certyfikatem CA projektu
zapewnia dodatkowo weryfikację serwera.

Oficjalna dokumentacja połączeń:
https://supabase.com/docs/guides/database/connecting-to-postgres
