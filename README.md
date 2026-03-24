# GitHub Method Miner

Herramienta que extrae y visualiza en tiempo real las palabras más usadas en nombres de métodos/funciones de repositorios de Python y Java publicados en GitHub.

```
┌─────────┐   API GitHub   ┌────────┐   Redis Sorted Set    ┌────────────┐
│  Miner  │ ─────────────► │ Redis  │ ◄──────────────────►  │ Visualizer │
│ (Python)│                │        │                       │ (Flask+SSE)│
└─────────┘                └────────┘                       └────────────┘
                          Productor ──────────────────────────── Consumidor
```

---

## Inicio rápido
Copiar el .env a la raiz del proyecto

Comando para iniciar docker compose:
```bash
docker compose up --build
```

Se habilitara la url **http://localhost:5000**, para abrirlo en el navegador.

Para detener docker compose:

```bash
docker compose down
```

---

## Variables de entorno

### Miner

| Variable       | Default | Descripción                                    |
|----------------|---------|------------------------------------------------|
| `REDIS_HOST`   | redis   | Hostname del servicio Redis                    |
| `REDIS_PORT`   | 6379    | Puerto Redis                                   |
| `GITHUB_TOKEN` | —       | Token opcional (aumenta el rate limit de 60→5000 req/h) |
| `TOP_REPOS`    | 30      | Cuántos repositorios procesar por ciclo        |
| `REPO_DELAY`   | 2.0     | Segundos de pausa entre repositorios           |
| `CYCLE_DELAY`  | 5       | Segundos de pausa entre ciclos completos       |


### Visualizer

| Variable     | Default | Descripción             |
|--------------|---------|-------------------------|
| `REDIS_HOST` | redis   | Hostname del servicio Redis |
| `REDIS_PORT` | 6379    | Puerto Redis            |

---

## Arquitectura y decisiones de diseño

### Componentes

#### Miner (`miner/miner.py`)
- Consulta la API de GitHub Search ordenando por `stars` descendente, separando Python y Java.
- Por cada repositorio, descarga los archivos `.py` y `.java` en la raíz.
- Extrae nombres de funciones/métodos:
  - **Python**: usa el módulo `ast` para un análisis sintáctico preciso (`FunctionDef`, `AsyncFunctionDef`).
  - **Java**: usa expresiones regulares capturando modificadores de acceso para evitar falsos positivos.
- Tokeniza los nombres respetando `camelCase` y `snake_case` con regex.
- Escribe en Redis usando **`ZINCRBY`** sobre una Sorted Set (`ranking_palabras`), lo que actualiza el contador de forma atómica.
- Usa **pipelines Redis** para agrupar escrituras y reducir latencia.
- El ciclo es infinito y continúa hasta ser detenido con `Ctrl+C` / `docker compose down`. 
- El proceso de lectura es cada 30 repos, el miner a llegar a 30 se reinicia para no estar minando todo el github. 

#### Redis
- Almacena los datos como una **Sorted Set** con score = frecuencia.
- `ZREVRANGE` permite obtener el top-N ordenado en O(log N + N).
- Los datos se persisten en disco (`appendonly yes`), sobreviviendo a reinicios.

#### Visualizer (`visualizer/app.py`)
- Servidor Flask con dos endpoints:
  - `GET /` → dashboard HTML.
  - `GET /stream?n=N` → **Server-Sent Events (SSE)**: empuja el ranking actualizado cada segundo sin polling del cliente.
  - `GET /api/ranking?n=N` → REST JSON (útil para depuración).
- El frontend usa **Chart.js** (gráfico de barras horizontal) + tabla con barras proporcionales.
- El slider TOP-N es parametrizable en tiempo real (5–50).

### Supuestos
1. Se mina sólo la **raíz** de cada repositorio para respetar el rate limit de GitHub.
2. Se excluyen stopwords genéricas (`get`, `set`, `is`, etc.) que no aportan información léxica significativa.
3. Palabras de 1 carácter y tokens no alfabéticos son descartados.
4. El miner no persiste qué repos ya procesó: reprocesa cada ciclo, lo que incrementa los contadores y simula uso continuo.

---

## Estructura del repositorio

```
github-miner/
├── docker-compose.yml
├── README.md
├── miner/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── miner.py
└── visualizer/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py
│   └── templates/
│   │  └── index.html
```

