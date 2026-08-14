# ADR-0011: Integracje Git (GitHub/GitLab) i tworzenie PR

- Status: przyjęty
- Data: 2026-08-13
- Etap: 9

## Kontekst

Produkt ma pozwalać na pracę z repozytoriami użytkownika: listowanie, przeglądanie i
**tworzenie PR/MR** — element „kodowania jak Claude". Wymogi: suwerenność (bez cudzych
API *modeli*; integracje kodu są OK), sekrety poza repo/magazynem, deny-all egress,
pełna testowalność bez sieci.

## Decyzja

### Token jako referencja do sekretu (nie plaintext)

Połączenie (`GitConnection`) przechowuje `token_ref` (np. `env:GITHUB_TOKEN`), nie
wartość tokenu. Token rozwiązywany jest z referencji przez dostawcę sekretów DOPIERO
przy operacji. Magazyn połączeń (InMemory/File JSON, zapis atomowy) trzyma tylko
metadane — spójne z `api_key_ref`/`api_token_ref`.

### Klienci dostawców nad wstrzykiwalnym transportem

`GitHubProvider`/`GitLabProvider` implementują wspólny `GitProvider` (lista repo,
utworzenie PR/MR) nad `GitTransport` (Protocol). Domyślny `HttpxGitTransport`; w
testach wstrzykiwany fake → zero sieci. GitLab wymaga URL-encode ścieżki projektu.

### Bramka egress (deny-all) na hoście dostawcy

`build_provider` wywołuje `check_endpoint_allowed(api_base, egress)` — host dostawcy

> **Aktualizacja (Etap 14/15b).** Powyższy opis jest historyczny: `build_provider` NIE woła
> już `check_endpoint_allowed`. Ta funkcja przepuszcza bez warunków „endpointy lokalne"
> (po samej NAZWIE, m.in. sufiksy `.local`/`.internal`) — bezpieczne dla lokalnego Ollamy,
> ale nie dla ścieżki niosącej token z prawem zapisu do repozytoriów. Git egzekwuje
> allowlistę własną, wąską bramką w `_endpoint_target` i od Etapu 15b korzysta ze
> współdzielonej warstwy anty-SSRF z **pinowaniem IP** — patrz
> [ADR-0020](0020-pinowanie-ip-anty-ssrf.md) i [GIT.md](../GIT.md).
musi być na `security.egress.allowlist`, inaczej `EgressError` → HTTP 403. Bez jawnej
zgody operatora Husarz nie łączy się z WAN (ta sama warstwa co router modeli).

### RBAC per operacja

`git:read` / `git:write` / `git:pr` (rola `operator`/`admin`). Tworzenie PR to akcja
o skutkach zewnętrznych — oddzielone uprawnienie `git:pr`. Wywołanie inicjuje
użytkownik platformy (nie rdzeń autonomicznie).

### Sekcja konfiguracji `git`

Opcjonalny `config/git.yaml` (`enabled`, `connections_path`). Domyślnie wyłączone.

## Konsekwencje

- (+) Praca z repo i PR bez naruszania suwerenności (token-ref, egress, brak sieci w testach).
- (+) Ten sam wzorzec co reszta (wstrzykiwalny backend, referencje sekretów, egress).
- (−) **PAT** zamiast pełnego OAuth — prostsze i sovereign-friendly dla pojedynczego
  operatora; dla trybu hostowanego (wielu użytkowników z własnymi tokenami) potrzebny
  OAuth + szyfrowane tokeny at-rest — odłożone.
- (−) PR zakłada istniejące gałęzie po stronie dostawcy; commit plików + push przez API
  (agent Kopijnik) to rozbudowa.
- (−) Egzekwowanie egress to warstwa aplikacji (dobór hosta); pełne wymuszenie sieciowe
  — NetworkPolicy/sandbox (Etap 6).

## Alternatywy odrzucone

- **Przechowywanie surowego tokenu w magazynie**: łamie „sekrety poza repo/magazynem"
  — odrzucone na rzecz referencji.
- **Lokalny `git` CLI w sandboxie**: cięższe (klucze, klonowanie); REST dostawcy jest
  lżejszy i wystarcza do listy+PR — CLI-w-sandboxie zostaje opcją na przyszłość.
- **OAuth od razu**: rejestracja aplikacji + callback — większa powierzchnia; PAT
  wystarcza dla MVP suwerennego.
