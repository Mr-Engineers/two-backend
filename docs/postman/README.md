# Import do Postmana

1. W Postmanie wybierz **Import** i dodaj `shops.postman_collection.json` oraz
   pliki `shop-pl.postman_environment.json`, `shop-de.postman_environment.json`,
   `shop-ru.postman_environment.json`.
2. Wybierz środowisko sklepu. W zmiennej `api_key` wpisz odpowiedni klucz z `.env`:
   `DEMO_API_KEY_SHOP_PL_AGENT`, `DEMO_API_KEY_SHOP_DE_AGENT` albo
   `DEMO_API_KEY_SHOP_RU_AGENT`. Pliki eksportu nie zawierają sekretów.
3. Uruchom backendy: `python scripts/run_shops.py --env-file .env`.
4. Wyślij `Health: ready`, następnie żądania katalogu. Żądania zakupowe
   oznaczone numerami 1–8 wykonuj po kolei. Skrypty odpowiedzi automatycznie
   zapisują identyfikatory koszyka, wyceny, zamówienia i klucz idempotencji
   w aktywnym środowisku.

Kolekcja obejmuje wszystkie 17 endpointów REST, `/docs`, `/openapi.json` oraz
przykłady JSON-RPC dla `/mcp`. Do testowania używaj adresu backendu, nie adresu
Supabase. Usuwanie pozycji i czyszczenie koszyka wykonuj przed checkoutem
w osobnym folderze. Nie uruchamiaj całej kolekcji bez wyboru odpowiednich
żądań: checkout kupuje produkty w demo i zmniejsza stan magazynowy.

PUT ustawia ilość absolutną. Opcjonalnie dodaj `expected_version` do jego
JSON oraz `expected_cart_version` do JSON wyceny, jako liczbę z odpowiedzi
koszyka. Opcjonalne parametry wyszukiwania i usuwania można włączyć w **Params**.
Wycenę kup w ciągu pięciu minut; przy ponowieniu tego samego checkoutu zachowaj
ten sam klucz idempotencji. Nowa wycena generuje nowy klucz.

Odtworzenie plików: `python scripts/generate_postman.py`. Generator sprawdza
kompletność endpointów względem routerów FastAPI.
