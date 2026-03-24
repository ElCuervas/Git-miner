import ast
import re
import time
import os
import requests
import redis
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [MINER] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

#===============Conexión Redis======================
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')
REDIS_KEY = 'ranking_palabras'
CYCLE_DELAY = int(os.getenv('CYCLE_DELAY', 5))       # segundos entre ciclos
REPO_DELAY  = float(os.getenv('REPO_DELAY', 2.0))    # segundos entre repos
TOP_REPOS   = int(os.getenv('TOP_REPOS', 30))        # cuántos repos procesar por ciclo

def conectar_redis():
    while True:
        try:
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            r.ping()
            log.info(f"Conectado a Redis en {REDIS_HOST}:{REDIS_PORT}")
            return r
        except Exception as e:
            log.warning(f"Redis no disponible ({e}), reintentando en 3s...")
            time.sleep(3)

db = conectar_redis()
#===============Conexión Redis======================


#===============Analizador Python====================
class AnalizadorPython(ast.NodeVisitor):
    def __init__(self):
        self.nombres = []

    def visit_FunctionDef(self, node):
        self.nombres.append(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self.nombres.append(node.name)
        self.generic_visit(node)
#===============Analizador Python====================

#===============Analizador Java======================
#Palabra a evitar para java
JAVA_KEYWORDS = {
    'if', 'for', 'while', 'switch', 'catch', 'return', 'else',
    'case', 'try', 'do', 'new', 'throw', 'assert', 'instanceof',
    'super', 'this', 'import', 'class', 'interface', 'enum',
}

def extraer_nombres_java(codigo):
    patron = (
        r'(?:public|protected|private|static|final|synchronized|\s)+'
        r'[\w\<\>\[\],\s]+\s+'
        r'([a-z][a-zA-Z0-9_]+)\s*\('
    )
    return [m for m in re.findall(patron, codigo) if m not in JAVA_KEYWORDS]
#===============Analizador Java======================

#===============Tokenizador de nombres===================
STOPWORDS = {
    # artículos / preposiciones
    'of', 'the', 'a', 'an', 'in', 'on', 'at', 'by', 'for', 'to', 'is',
    # palabras demasiado genéricas en código
    'get', 'set', 'is', 'has', 'do',
    # palabras reservadas que se cuelan como tokens
    'if', 'or', 'and', 'not', 'as', 'no',
}

def split_words(name: str) -> list[str]:
    #Separador por guiones bajos
    partes = name.split('_')
    tokens = []
    for parte in partes:
        #Separador de camelCase/PascalCase
        segmentos = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', parte)
        segmentos = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', segmentos)
        for seg in segmentos.split():
            w = seg.lower()
            if len(w) > 1 and w not in STOPWORDS and w.isalpha():
                tokens.append(w)
    return tokens
#===============Tokenizador de nombres===================

#==================Headers y get=========================
def _headers():
    h = {'User-Agent': 'GitHub-Method-Miner/1.0'}
    if GITHUB_TOKEN:
        h['Authorization'] = f'token {GITHUB_TOKEN}'
    return h

def _get(url, params=None, timeout=15):
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=timeout)
        return r
    except requests.RequestException as e:
        log.warning(f"Request error: {e}")
        return None
#==================Headers y get=========================

#==================Obtencion de repos====================
def obtener_repos(paginas=1) -> list:
    #Devuelve repos más populares de Python y Java.
    repos = []
    for lang in ('python', 'java'):
        for page in range(1, paginas + 1):
            r = _get(
                'https://api.github.com/search/repositories',
                params={'q': f'language:{lang}', 'sort': 'stars', 'order': 'desc',
                        'per_page': 30, 'page': page}
            )
            if r is None or r.status_code != 200:
                log.warning(f"No se pudo obtener repos ({lang} p{page}): {getattr(r,'status_code','N/A')}")
                break
            items = r.json().get('items', [])
            repos.extend(items)
            if not items:
                break
    # Ordenar por estrellas descendente y eliminar duplicados por id
    seen = set()
    unique = []
    for repo in sorted(repos, key=lambda x: x.get('stargazers_count', 0), reverse=True):
        if repo['id'] not in seen:
            seen.add(repo['id'])
            unique.append(repo)
    return unique
#==================Obtencion de repos====================

#===================Extraccion de archivos====================
def procesar_archivo(file_info: dict) -> int:
    #Descarga y analiza un archivo fuente
    #Retorna número de palabras extraídas
    url = file_info.get('download_url')
    if not url:
        return 0
    r = _get(url)
    if r is None or r.status_code != 200:
        return 0

    contenido = r.text
    nombres = []

    if file_info['name'].endswith('.py'):
        try:
            tree = ast.parse(contenido)
            visor = AnalizadorPython()
            visor.visit(tree)
            nombres = visor.nombres
        except SyntaxError:
            pass
    elif file_info['name'].endswith('.java'):
        nombres = extraer_nombres_java(contenido)

    palabras_nuevas = 0
    pipe = db.pipeline()
    for nombre in nombres:
        for palabra in split_words(nombre):
            pipe.zincrby(REDIS_KEY, 1, palabra)
            palabras_nuevas += 1
    pipe.execute()
    return palabras_nuevas
#===================Extraccion de archivos====================

#===================Proceso del miner==========================
# Carpetas que no aportan código fuente
DIRS_IGNORADAS = {
    'node_modules', '.git', 'venv', '__pycache__', '.venv',
    'dist', 'build', 'target', '.idea', '.vscode',
    'test', 'tests', 'vendor', 'third_party', 'thirdparty',
}

def minar_repo(repo: dict) -> int:
    #Inicia el minado recursivo desde la raíz del repositorio
    return _minar_recursivo(repo['full_name'], '')

def _minar_recursivo(repo_full_name: str, path: str) -> int:
    #Explora carpetas y archivos recursivamente buscando .py y .java
    url = f'https://api.github.com/repos/{repo_full_name}/contents/{path}'
    r = _get(url)
    if r is None or r.status_code != 200:
        return 0

    #Maneja rate limit de GitHub
    if r.status_code == 403:
        reset = int(r.headers.get('X-RateLimit-Reset', time.time() + 60))
        espera = max(reset - int(time.time()), 10)
        log.warning(f"Rate limit alcanzado. Esperando {espera}s...")
        time.sleep(espera)
        return 0

    items = r.json()
    if not isinstance(items, list):
        return 0

    total = 0
    for item in items:
        if item['type'] == 'dir':
            if item['name'] not in DIRS_IGNORADAS:
                total += _minar_recursivo(repo_full_name, item['path'])
        elif item['type'] == 'file':
            if item['name'].endswith('.py') or item['name'].endswith('.java'):
                total += procesar_archivo(item)
    return total
#===================Proceso del miner==========================


#===================Bucle principal==========================
def main():
    ciclo = 0
    while True:
        ciclo += 1
        log.info(f"{'='*55}")
        log.info(f"CICLO #{ciclo}  —  TOP {TOP_REPOS} REPOSITORIOS")
        log.info(f"{'='*55}")

        repos = obtener_repos()
        if not repos:
            log.warning("Sin repositorios, esperando 60s...")
            time.sleep(60)
            continue

        for i, repo in enumerate(repos[:TOP_REPOS], 1):
            nombre = repo['full_name']
            stars  = repo.get('stargazers_count', 0)
            log.info(f"[{i:>2}/{TOP_REPOS}] ⭐ {stars:,}  →  {nombre}")

            palabras = minar_repo(repo)
            log.info(f"        +{palabras} tokens indexados")

            time.sleep(REPO_DELAY)

        total_unicas = db.zcard(REDIS_KEY)
        log.info(f"\nCiclo #{ciclo} completado. Palabras únicas en Redis: {total_unicas}")
        log.info(f"Esperando {CYCLE_DELAY}s antes del próximo ciclo...\n")
        time.sleep(CYCLE_DELAY)
#===================Bucle principal==========================


if __name__ == '__main__':
    main()