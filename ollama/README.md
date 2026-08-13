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
