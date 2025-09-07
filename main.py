import os
import logging
import uuid
from datetime import datetime, timezone

import functions_framework
import google.auth
import google.auth.transport.requests
import requests
from google.oauth2 import id_token
from cloudevents.http import CloudEvent
import firebase_admin
from firebase_admin import credentials, firestore

# --- Configuração de Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Inicialização do Firebase ---
try:
    firebase_admin.initialize_app()
    db = firestore.client()
    logging.info("Conexão com o Firestore estabelecida com sucesso.")
except Exception as e:
    logging.error(f"Erro ao inicializar o Firebase Admin: {e}")
    db = None

# --- Carregamento de Variáveis de Ambiente ---
SCRAPER_SERVICE_URL = os.environ.get("SCRAPER_SERVICE_URL")
if not SCRAPER_SERVICE_URL:
    logging.error("A variável de ambiente SCRAPER_SERVICE_URL não foi definida.")
    raise ValueError("SCRAPER_SERVICE_URL must be set.")

def get_auth_token():
    """Obtém um token de identidade do Google para invocar serviços do Cloud Run."""
    try:
        auth_req = google.auth.transport.requests.Request()
        # O fetch_id_token é o método recomendado para obter um token de identidade
        # para um 'audience' (serviço alvo) específico.
        token = id_token.fetch_id_token(auth_req, SCRAPER_SERVICE_URL)
        return token
    except Exception as e:
        logging.error(f"Erro ao gerar o token de autenticação: {e}")
        raise

@functions_framework.cloud_event
def trigger_scraper(cloud_event: CloudEvent):
    """
    Cloud Function acionada pela criação de um novo documento no Firestore
    na coleção 'monitor_results'.
    """
    if not db:
        logging.critical("Cliente Firestore não está disponível. A função não pode continuar.")
        return

    resource_string = cloud_event["subject"]
    doc_id = resource_string.split('/')[-1]
    
    # --- Registro de Log no Firestore ---
    log_collection = db.collection('system_logs')
    log_doc_ref = log_collection.document()
    log_data = {
        'run_id': str(uuid.uuid4()),
        'module': 'trigger-scraper',
        'target_doc_id': doc_id,
        'start_time': datetime.now(timezone.utc),
        'end_time': None,
        'status': 'processing',
        'details': f'Iniciando a invocação do scraper para o documento: {doc_id}'
    }
    log_doc_ref.set(log_data)
    logging.info(f"Novo documento detectado: {doc_id}. Log de sistema criado: {log_doc_ref.id}")

    try:
        # Obter token de autenticação para o Cloud Run
        id_token = get_auth_token()
        headers = {"Authorization": f"Bearer {id_token}"}

        # Montar a URL do endpoint alvo
        target_url = f"{SCRAPER_SERVICE_URL}/scrape/by-doc-id/{doc_id}"

        # Invocar o serviço de scraper
        logging.info(f"Invocando o serviço de scraper em: {target_url}")
        response = requests.post(target_url, headers=headers, timeout=300) # 5 min timeout

        # Verificar o resultado da chamada
        response.raise_for_status()  # Lança uma exceção para códigos de status HTTP 4xx/5xx

        logging.info(f"Serviço de scraper invocado com sucesso para o doc_id: {doc_id}. Resposta: {response.json()}")
        
        # Atualizar log para sucesso
        log_data.update({
            'end_time': datetime.now(timezone.utc),
            'status': 'success',
            'details': f'Serviço de scraper invocado com sucesso. Status da resposta: {response.status_code}.'
        })

    except requests.exceptions.RequestException as e:
        error_message = f"Erro de HTTP/Rede ao invocar o scraper: {e}"
        logging.error(error_message)
        log_data.update({
            'end_time': datetime.now(timezone.utc),
            'status': 'failed',
            'details': error_message,
            'error_details': str(e.response.text) if e.response else 'No response from server'
        })
        raise  # Propaga a exceção para o Cloud Functions gerenciar retentativas

    except Exception as e:
        error_message = f"Ocorreu um erro inesperado: {e}"
        logging.error(error_message, exc_info=True)
        log_data.update({
            'end_time': datetime.now(timezone.utc),
            'status': 'failed',
            'details': error_message
        })
        raise

    finally:
        # Garante que o log seja sempre atualizado
        log_doc_ref.update(log_data)
        logging.info(f"Log de sistema finalizado para o doc_id: {doc_id} com status: {log_data['status']}")

