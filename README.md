# Camera Event Listener

Este projeto implementa um listener contínuo para receber todos os eventos de câmeras compatíveis com o protocolo CGI Event Manager:

GET /cgi-bin/eventManager.cgi?action=attach&codes=All&heartbeat=5


A aplicação se conecta via HTTP Digest Authentication (com fallback para Basic), processa o stream de eventos no formato key=value, ignora apenas heartbeats e registra qualquer evento recebido da câmera.

Funcionalidades

Conexão contínua ao eventManager.cgi com suporte a todos os eventos (All)

Suporte a DigestAuth e fallback automático para BasicAuth

Reconexão automática com exponential backoff + jitter

Parser robusto para stream key=value com ruído multipart

Heartbeat configurável para detectar desconexões

Logs detalhados de todos os eventos

Encerramento limpo via sinais (SIGINT, SIGTERM)

Estrutura
camera-event-listener/
│── listener.py        # Script principal
│── .env.example       # Exemplo de variáveis de ambiente
│── README.md          # Este arquivo

Configuração

Crie um arquivo .env (ou exporte variáveis de ambiente):

CAM_IP=192.168.1.108
CAM_USER=admin
CAM_PASS=admin123

# Opções de conexão
USE_HTTPS=false
VERIFY_TLS=false
TLS_CA_BUNDLE=

# Listener
HEARTBEAT=5
CODES=All

# Backoff
BACKOFF_BASE=1.5
BACKOFF_MAX=60

# Logs
LOG_LEVEL=INFO


Observação: Altere sempre as credenciais padrão para senhas seguras.

Execução
Com Python diretamente
python3 listener.py

Interromper

Use CTRL+C ou envie SIGTERM para o processo.

Exemplo de Saída
2025-08-21T15:33:42 INFO EVENTO!!! VideoMotion [Start] ch=1 ts=2025-08-21 15:33:41 payload={"Code": "VideoMotion", "action": "Start", "index": "1", "DetectTime": "2025-08-21 15:33:41"}
2025-08-21T15:33:47 INFO EVENTO!!! AlarmLocal [Stop] ch=2 ts=2025-08-21 15:33:47 payload={"Code": "AlarmLocal", "action": "Stop", "index": "2", "DateTime": "2025-08-21 15:33:47"}

Personalização

Alterar CODES=All para filtrar apenas eventos específicos (ex.: VideoMotion,AlarmLocal)

Configurar USE_HTTPS=true + TLS_CA_BUNDLE para conexões seguras

Ajustar HEARTBEAT conforme o firmware da câmera

Integrar com sistemas externos via logs, filas de mensagens ou webhooks

Boas Práticas

Alterar credenciais padrão

Atualizar regularmente o firmware da câmera

Usar HTTPS sempre que possível

Restringir acesso por IP e portas expostas
