# ===========================================================================
# Csvtraitement — image « batch » sans interface graphique
#
#   docker build -t csvtraitement:0.2.0 .
#   docker run --rm -v "$PWD/in:/data/in" -v "$PWD/out:/data/out" csvtraitement:0.2.0
#
# L'outil n'est pas un service web : c'est une application de bureau (Tkinter)
# doublée d'une CLI. L'image n'embarque que la CLI, pour le cas d'usage serveur :
# nettoyer chaque nuit un dossier d'exports ERP déposés par un SFTP ou un partage.
# Tkinter n'est pas installé : main.py ne l'importe qu'en mode GUI.
# ===========================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dépendances d'abord : la couche reste en cache tant que requirements.txt ne change pas.
COPY requirements.txt ./
RUN pip install -r requirements.txt pyarrow odfpy

COPY main.py ./
COPY core ./core
COPY utils ./utils
COPY ui/__init__.py ./ui/__init__.py

# Utilisateur non root (exigé par la plupart des politiques Kubernetes).
RUN useradd --uid 10001 --create-home app && mkdir -p /data/in /data/out \
    && chown -R app /data
USER 10001

# Par défaut : tous les CSV de /data/in -> CSV corrigés dans /data/out (séparateur ;).
ENTRYPOINT ["python", "main.py", "cli"]
CMD ["--batch", "/data/in", "--pattern", "*.csv", "--fix", "-o", "/data/out", "--format", "csv", "--csv-separator", ";", "--quiet"]
