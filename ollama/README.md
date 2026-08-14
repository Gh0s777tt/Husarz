# ollama/ — customowy model lokalny Husarza

Definicja modelu `husarz` (czat + kodowanie) budowanego lokalnie w [Ollamie](https://ollama.com).
Persona hetmana (PL) jest zaszyta w [`Husarz.Modelfile`](Husarz.Modelfile) (`SYSTEM`),
a baza (`FROM`) jest wymienna — to jedyne miejsce wyboru silnika.

## Wymagania

- Zainstalowana Ollama (Windows/Linux/macOS): <https://ollama.com/download>.
- Miejsce na wagi (bazowy model, np. `qwen2.5-coder:7b` ≈ 4–5 GB) — trafiają do
  lokalnego magazynu Ollamy (nie do repo; `models/` jest gitignored).

## Budowa i uruchomienie

```bash
# 1) Pobierz bazę (raz) i zbuduj customowy model 'husarz'
ollama pull qwen2.5-coder:7b
ollama create husarz -f ollama/Husarz.Modelfile

# 2) Szybki test w terminalu
ollama run husarz "Napisz i wyjaśnij funkcję sumującą listę w Pythonie."

# 3) Ollama wystawia API OpenAI-compat na http://localhost:11434/v1
#    — dokładnie to, czego oczekuje config/models.yaml (model 'husarz-local').
```

## Wpięcie do Husarza (już skonfigurowane)

`config/models.yaml` zawiera model `husarz-local` (backend `ollama`, endpoint
`http://localhost:11434/v1`, `model: husarz`) i ustawia go jako **model czatu**
(`models.chat: husarz-local`). Po zbudowaniu modelu i uruchomieniu API:

```bash
python -m husarz.launcher.cli up --profile dev
```

…zakładka **Czat** w konsoli (`http://127.0.0.1:8000/`) rozmawia bezpośrednio z Twoim
lokalnym modelem (`POST /api/chat`). Zmiana bazy lub parametrów = edycja Modelfile i
`ollama create husarz ...` ponownie — **bez zmian w kodzie**.

## Wymiana silnika (przykłady)

Zmień tylko linię `FROM` w [`Husarz.Modelfile`](Husarz.Modelfile):

| Cel | `FROM` |
|-----|--------|
| Domyślny (kod + PL) | `qwen2.5-coder:7b` |
| Lepsza jakość (więcej VRAM) | `qwen2.5-coder:14b` |
| Mocniejszy ogólny czat | `llama3.1:8b` |
| Najlepszy polski | model Bielika dostępny w Ollamie |

Następnie: `ollama create husarz -f ollama/Husarz.Modelfile`.

> **Uwaga o stop-tokenach.** `PARAMETER stop` w Modelfile jest dostrojony do formatu
> ChatML (qwen). Zmieniając `FROM` na bazę nie-ChatML (llama3.1, Bielik), dostosuj lub
> usuń te linie — inaczej pozostają nieaktywne (Ollama i tak używa szablonu z GGUF).
>
> **Źródło prawdy dla parametrów żądania.** `temperature`/`top_p` w Modelfile to wartości
> domyślne wypalone w modelu, ale przy wywołaniach przez Husarza nadrzędne są
> `params` z `config/models.yaml` (klient wysyła je jawnie w każdym żądaniu). Zmianę
> temperatury dla platformy rób w configu, nie tylko w Modelfile.

## Rozwiązywanie problemów

### GPU NVIDIA 50xx (Blackwell) — `cudaMalloc failed` mimo wolnego VRAM

Objaw: model 7B nie wchodzi na GPU (`cudaMalloc failed: out of memory` przy alokacji
~4.4 GB), mimo że `nvidia-smi` pokazuje kilkanaście GB wolnego VRAM; częściowy offload
(`num_gpu`) wywala `CUDA error: shared object initialization failed`. Modele ≤3B ładują się
100% na GPU bez problemu.

Przyczyna: na bardzo nowych sterownikach (obserwowane: 595.97 / CUDA 13.2) + Blackwell
backend CUDA Ollamy (testowane 0.32.9 — najnowsze) ma **limit pojedynczej alokacji ~4 GB** —
większy bufor wag zawodzi. To bug warstwy sterownik/CUDA, nie brak VRAM.

Obejścia (od najlepszego):
1. **Zaktualizuj/zmień sterownik NVIDIA** na stabilną wersję produkcyjną — właściwy fix dla 7B.
2. **Mniejsza baza w Modelfile**: `FROM qwen2.5-coder:3b` (≈2.4 GB, mieści się pod limitem,
   działa 100% na GPU, dobra jakość kodu). Po naprawie sterownika wróć na `7b`.
3. **CPU** (`PARAMETER num_gpu 0`) — działa dla każdego rozmiaru, ale wolniej.

Sprawdź, gdzie model się załadował: `ollama ps` (kolumna `PROCESSOR` = `100% GPU` / `CPU`).

### `ollama create -f` myli `FROM model:tag` ze ścieżką pliku (Windows)

W niektórych wersjach `ollama create husarz -f ollama/Husarz.Modelfile` interpretuje
`FROM qwen2.5-coder:7b` jako ścieżkę (`...\ollama\qwen2.5-coder:7b`) i pada. Obejście:
zbuduj przez API HTTP `POST /api/create` z jawnymi polami `from`/`system`/`parameters`,
albo zaktualizuj Ollamę.
