#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Listener contínuo para **todos** os eventos via CGI:
  GET /cgi-bin/eventManager.cgi?action=attach&codes=All&heartbeat=5

- Credenciais e host são carregados de .env (ou variáveis de ambiente).
- Usa HTTP Digest Auth por padrão; opcional fallback p/ Basic.
- Parser robusto para stream (key=value) com ruído multipart.
- Reconecta automaticamente com exponential backoff + jitter.
- Ignora heartbeats e registra todos os eventos (qualquer Code).
"""

import os
import time
import json
import random
import signal
import logging
from typing import Dict, Iterator, Optional

import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth

# ===========================
# .ENV
# ===========================
try:
    from dotenv import load_dotenv  # pip install python-dotenv
    load_dotenv()
except Exception:
    pass  # segue sem python-dotenv, se não instalado

CAM_IP = os.getenv("CAM_IP", "192.168.1.108").strip()
USERNAME = os.getenv("CAM_USER", "admin").strip()
PASSWORD = os.getenv("CAM_PASS", "admin123").strip()

USE_HTTPS = os.getenv("USE_HTTPS", "false").strip().lower() in ("1", "true", "yes", "y")
VERIFY_TLS = os.getenv("VERIFY_TLS", "false").strip().lower() in ("1", "true", "yes", "y")
CA_BUNDLE = os.getenv("TLS_CA_BUNDLE")  # caminho para CA customizada (opcional)

HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT", "5"))

# ⬇ Padrão agora é **All** para receber qualquer evento (alguns firmwares aceitam também "[All]").
CODES = os.getenv("CODES", "All").strip()

REQUEST_TIMEOUT = None   # manter stream aberto
CONNECT_TIMEOUT = 10

BACKOFF_BASE = float(os.getenv("BACKOFF_BASE", "1.5"))
BACKOFF_MAX = float(os.getenv("BACKOFF_MAX", "60"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("event-listener")

# ===========================
# CONTROLE DE SAÍDA LIMPA
# ===========================
_stop = False
def _handle_signal(signum, frame):
    global _stop
    _stop = True
    logger.info("Sinal %s recebido, encerrando…", signum)

for _sig in (signal.SIGINT, signal.SIGTERM):
    try:
        signal.signal(_sig, _handle_signal)
    except Exception:
        pass  # Windows pode não suportar alguns sinais

# ===========================
# PARSER DO STREAM (ROBUSTO)
# ===========================
def iter_event_lines(resp: requests.Response) -> Iterator[str]:
    """
    Itera linha a linha no stream. Normaliza para str, ignora None/blank,
    remove '\r', e descarta delimitadores/headers de multipart.
    """
    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue

        # Normaliza para str
        if isinstance(raw, (bytes, bytearray)):
            try:
                line = raw.decode("utf-8", errors="ignore")
            except Exception:
                continue
        else:
            line = str(raw)

        line = line.strip().strip("\r")
        if not line:
            continue

        # Ignora artefatos multipart e headers
        if line.startswith("--"):  # ex.: --myboundary
            continue
        if ":" in line and "=" not in line:
            # headers de parte (ex.: Content-Type: text/plain)
            continue

        yield line

def parse_kv_block(lines: Iterator[str]) -> Iterator[Dict[str, str]]:
    """
    Constrói eventos a partir de pares key=value consecutivos.
    Um bloco termina ao ver próxima 'Code=' ou 'heartbeat'.
    Tolerante a lixo: ignora linhas sem '=' e tipos inesperados.
    """
    current: Dict[str, str] = {}
    for line in lines:
        if line is None:
            continue
        if not isinstance(line, str):
            try:
                line = str(line)
            except Exception:
                continue
        line = line.strip()
        if not line:
            continue

        # Heartbeat
        if line.lower() == "heartbeat":
            if current:
                yield current
                current = {}
            yield {"heartbeat": "1"}
            continue

        if "=" not in line:
            # Não é par key=value, ignora silenciosamente
            continue

        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()

        # Novo evento inicia com Code=...
        if k == "Code" and current:
            yield current
            current = {}

        current[k] = v

    if current:
        yield current

# ===========================
# CLIENTE CGI
# ===========================
def build_base_url(ip: str) -> str:
    scheme = "https" if USE_HTTPS else "http"
    return f"{scheme}://{ip}/cgi-bin/eventManager.cgi"

def listen_once(session: requests.Session) -> Optional[int]:
    """
    Abre uma conexão e consome o stream até erro/desconexão.
    Retorna o status HTTP (para diagnósticos) ou None em falha de conexão.
    """
    base = build_base_url(CAM_IP)
    params = {
        "action": "attach",
        "codes": CODES,                       # agora padrão "All" (ou use [All])
        "heartbeat": str(HEARTBEAT_SECONDS),  # dispositivo envia 'heartbeat' periódico
    }

    verify_param = CA_BUNDLE if CA_BUNDLE else VERIFY_TLS

    logger.info("Conectando a %s com params=%s", base, params)
    try:
        # Tenta Digest primeiro; se 401, faz fallback p/ Basic
        auth_digest = HTTPDigestAuth(USERNAME, PASSWORD)
        with session.get(
            base,
            params=params,
            auth=auth_digest,
            stream=True,
            timeout=(CONNECT_TIMEOUT, REQUEST_TIMEOUT),
            verify=verify_param,
            headers={"Accept": "text/plain"}
        ) as resp:
            if resp.status_code == 401:
                resp.close()
                with session.get(
                    base,
                    params=params,
                    auth=HTTPBasicAuth(USERNAME, PASSWORD),
                    stream=True,
                    timeout=(CONNECT_TIMEOUT, REQUEST_TIMEOUT),
                    verify=verify_param,
                    headers={"Accept": "text/plain"}
                ) as resp2:
                    logger.info(
                        "HTTP %s (Basic) conectado (Transfer-Encoding: %s, Content-Type: %s)",
                        resp2.status_code,
                        resp2.headers.get("Transfer-Encoding"),
                        resp2.headers.get("Content-Type")
                    )
                    resp2.raise_for_status()
                    return _consume(resp2)

            logger.info(
                "HTTP %s conectado (Transfer-Encoding: %s, Content-Type: %s)",
                resp.status_code,
                resp.headers.get("Transfer-Encoding"),
                resp.headers.get("Content-Type")
            )
            resp.raise_for_status()
            return _consume(resp)

    except requests.exceptions.RequestException as e:
        logger.error("Falha na conexão/stream: %s", e)
        return None

def _consume(resp: requests.Response) -> int:
    lines = iter_event_lines(resp)
    for event in parse_kv_block(lines):
        if _stop:
            logger.info("Encerrando por solicitação…")
            return resp.status_code

        # Ignora apenas heartbeat; **não** filtra por tipo de evento
        if "heartbeat" in event:
            logger.debug("heartbeat")
            continue

        code    = event.get("Code", "")
        action  = event.get("action", "")
        channel = event.get("index") or event.get("Channel") or event.get("channel") or "?"
        ts      = event.get("DetectTime") or event.get("DateTime") or event.get("time") or ""

        # Loga qualquer evento
        logger.info(
            "EVENTO!!! %s [%s] ch=%s ts=%s payload=%s",
            code, action, channel, ts, json.dumps(event, ensure_ascii=False)
        )

    logger.warning("Stream encerrado pelo servidor.")
    return resp.status_code

def run_forever():
    """
    Loop de reconexão com backoff exponencial + jitter.
    """
    backoff = 1.0
    with requests.Session() as session:
        while not _stop:
            status = listen_once(session)
            if _stop:
                break

            sleep_s = min(BACKOFF_MAX, backoff * (1.0 + random.random()))
            logger.warning("Reconectando em %.1fs… (status=%s)", sleep_s, status)
            time.sleep(sleep_s)
            backoff = min(BACKOFF_MAX, backoff * BACKOFF_BASE)

        logger.info("Listener finalizado.")

if __name__ == "__main__":
    try:
        run_forever()
    except KeyboardInterrupt:
        pass
