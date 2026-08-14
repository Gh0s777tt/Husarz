# ADR-0018: Trwała pamięć (SQLite) + szyfrowanie at-rest

- Status: przyjęty
- Data: 2026-08-13
- Etap: 14b

## Kontekst

ADR-0017 (Etap 14) dostarczył wektorowy `EmbeddingRagBackend`, ale świadomie ODŁOŻYŁ
trwałość i szyfrowanie at-rest do 14b — bo szyfrowanie bez przewleczenia `SecretsProvider`
do produkcji byłoby teatrem (klucz nierozwiązywalny; `create_app`/`cli` podawały
`NullSecretsProvider`). Etap 14b domyka to RAZEM: trwały magazyn, szyfrowanie i wiązanie
sekretów w jednym kroku, tak by `encrypt_at_rest: true` było realne, nie martwym polem.

## Decyzja

### `SqliteVectorStore` — trwałość bez serwera

Nowy `VectorStore` (za NIEZMIENIONYM protokołem) na stdlib `sqlite3`, jeden plik pod
`data_dir/memory/<collection>.db`. Izolacja: `search`/`count` filtrują po `namespace`
(WHERE). Dedup po `(namespace, id)`; ewikcja FIFO po `max_items` (anty-OOM). Zapis atomowy
pod `threading.Lock` (`check_same_thread=False` → bezpieczny w puli wątków). Brak serwera,
brak nowej usługi — realna „pamięć długoterminowa" (przeżywa restart). Wybór magazynu:
`RagBackendConfig.store ∈ {in_memory, sqlite}`.

### Szyfrowanie at-rest CAŁEGO rekordu (AES-256-GCM)

`Cipher` (Protocol, wstrzykiwalny): `IdentityCipher` (dev, tylko `encrypt_at_rest=false`)
i `AesGcmCipher` (lazy import `cryptography`, opcjonalny extra `husarz[memory]`).
Szyfrujemy CAŁY rekord — **`item_id` + tekst + metadane + WEKTOR** — bo inwersja embeddingu
odtwarza treść/PII, więc jawny wektor obok zaszyfrowanego tekstu byłby luką. Nonce 96-bit per
rekord, **`AAD = namespace`** wiąże szyfrogram z kolekcją (anti-swap — rekordu nie da się
przenieść ani odszyfrować jako innej kolekcji). Klucz (DEK, 32 B) = SHA-256 sekretu z referencji
(`encryption_key_ref`) rozwiązywanej przez `SecretsProvider`. Scoring deszyfruje rekordy
przed cosinusem (koszt O(N) — stąd `max_items` jako sufit).

**Zaślepiony klucz wiersza (`Cipher.blind_id`).** Produkcyjny `item_id` = `SHA-256(text)`
(dedup po treści). Gdyby trafił jawnie do klucza wiersza SQLite, byłby deterministycznym,
niesolonym odciskiem treści — dla PII niskiej entropii (PESEL, e-mail, telefon) dawałby
membership-oracle i słownikowe odzyskanie plaintextu BEZ klucza AES, obalając cel at-rest.
Dlatego jawna kolumna `id` to `blind_id = HMAC-SHA256(DEK, namespace || 0x00 || item_id)`:
deterministyczna (zachowuje dedup i `UNIQUE(namespace,id)`), namespace'owana (brak korelacji
tego samego tekstu między kolekcjami) i bez DEK nieodwracalna. Autorytatywny `item_id` żyje
w zaszyfrowanym blobie (search zwraca go po odszyfrowaniu) — co wiąże też `id`↔treść w obrębie
kolekcji. Dla `IdentityCipher` (at-rest wyłączony) `blind_id` zwraca surowy id (parytet z RAM).

### Przewleczenie sekretów do produkcji (domknięcie blockera)

`cli._cmd_up` → `create_app(secrets=_SchemeSecrets())` → `_build_stack` →
`build_tool_loop(secrets=, data_dir=)` → `build_tools(secrets=, data_dir=)` →
`BuildContext` → `_build_rag` → `build_rag_backend`. Teraz `encryption_key_ref` realnie się
rozwiązuje w produkcji. `data_dir` (z `platform.data_dir`) daje konfigurowalną, izolowaną
lokalizację pliku (zero-hardcode).

### Bramki fail-closed (przy budowie, zanim backend jest używalny)

- `sqlite` + at-rest (rozwiązane) + brak/nierozwiązywalny klucz → błąd PL (NIGDY cichy plaintext).
- `security.encryption.at_rest=true` + rag `encrypt_at_rest=false` dla `sqlite` → błąd
  (globalny niezmiennik nie może być wyłączony lokalnie; `None` dziedziczy z globalnego).
- `IdentityCipher` dozwolony WYŁĄCZNIE gdy at-rest wyłączony (dev).
- Brak praw zapisu do `data_dir` (np. read-only FS frozen binarki) → czytelny `RagBackendError`.

## Konsekwencje

- (+) Realna trwała, szyfrowana pamięć — niezmiennik at-rest egzekwowany, nie deklarowany.
  Test dowodzi, że plik `.db` NIE zawiera jawnego tekstu/metadanych/wektora.
- (+) Sekrety przewleczone raz — korzysta z nich każdy przyszły komponent at-rest.
- (−) DEK = SHA-256 sekretu (KDF-lite) — bez soli/rotacji; wystarcza na MVP, pełne KMS/rotacja
  odłożone. Scoring O(N) deszyfrowań (bez indeksu ANN) — sufit `max_items`.
- (−) `cryptography` to opcjonalny extra — bramka jakości instaluje `[dev,memory]`, a test
  at-rest jest twardym wymogiem (nie skip). Fail-closed przy budowie: `build_cipher` przy
  at-rest sprawdza dostępność `cryptography` (czytelny błąd PL, nie odroczony ImportError).
- (−) **Rozwiązywanie sekretów:** schemat `encryption_key_ref` dopuszcza `env:/file:/vault:/sops:`
  (spójnie z resztą projektu), ale dostarczony resolver CLI (`_SchemeSecrets`) obsługuje realnie
  `env:`/`file:`; `vault:`/`sops:` wymagają `SecretsProvider`, który je implementuje (przyszłe).
  Zachowanie jest fail-closed (brak klucza → błąd, nigdy cichy plaintext), nie ma wycieku.
- (+) **Cykl życia połączenia — DOMKNIĘTE (follow-up po 14b):** protokoły `VectorStore`/`RagBackend`
  (oraz `RagTool`/`ToolDispatcher`/`ToolLoop`) mają `close()`; przy `POST /api/config/runtime`
  STARA pętla jest zamykana po atomowej podmianie (`app._build_stack` zwraca pętlę, `config_apply`
  woła `old_loop.close()` — best-effort, tłumi błędy). Dzięki temu uchwyt pliku sqlite nie wycieka
  przy rekonfiguracji runtime. `SqliteVectorStore.close()` jest idempotentne; magazyny w RAM = no-op.

## Alternatywy odrzucone

- **Szyfrowanie tylko payloadu (jawny wektor)**: częściowa inwersja embeddingu = wyciek PII.
- **SQLCipher / szyfrowany wolumen (LUKS/BitLocker)**: zależność natywna/ops — odłożone;
  app-level koperta jest przenośna i testowalna.
- **`encryption_key_ref` globalny w `EncryptionConfig`**: promocja do globalnego, gdy pojawi
  się drugi komponent at-rest; MVP trzyma go lokalnie w `RagBackendConfig`.
- **pgvector jako trwały store teraz**: wymaga serwera Postgres — przyszły adapter za `VectorStore`.
