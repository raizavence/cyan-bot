# Cyan — Bot de Briefing da Copack

Assistente de briefing de arte da Copack Embalagens Sustentáveis.  
Recebe materiais bagunçados de um pedido (prints do WhatsApp, imagens, PDFs) e transforma em um briefing estruturado e pronto para a equipe de arte final.

---

## Pré-requisitos

- Python 3.10+
- `poppler` instalado no sistema (para conversão de PDFs em imagem)
  - Ubuntu/Debian: `sudo apt install poppler-utils`
  - macOS: `brew install poppler`
- Conta no Discord com um bot criado no [Discord Developer Portal](https://discord.com/developers/applications)
- Chave de API da OpenAI com acesso ao GPT-4o

---

## Instalação

```bash
# Clone ou copie os arquivos para o servidor
cd cyan-bot

# Crie um ambiente virtual
python -m venv .venv
source .venv/bin/activate

# Instale as dependências
pip install -r requirements.txt
```

---

## Configuração

Copie o arquivo de exemplo e preencha com os valores reais:

```bash
cp .env.example .env
```

| Variável | Como obter |
|---|---|
| `DISCORD_TOKEN` | Discord Developer Portal → seu app → Bot → Token |
| `OPENAI_API_KEY` | platform.openai.com → API keys |
| `BRIEFING_CHANNEL_ID` | No Discord: clique direito no canal → Copiar ID (modo Dev ativo) |
| `ANALYSIS_CHANNEL_ID` | Idem para o canal #análise-de-arquivos |

> Para ativar o modo desenvolvedor no Discord: Configurações → Avançado → Modo desenvolvedor.

---

## Permissões necessárias para o bot no Discord

No Developer Portal, em **OAuth2 → URL Generator**, marque:

- Scopes: `bot`, `applications.commands`
- Bot Permissions:
  - `Read Messages / View Channels`
  - `Send Messages`
  - `Read Message History`
  - `Attach Files`
  - `Use Slash Commands`

---

## Executando

```bash
python main.py
```

O bot aparecerá online no servidor e os comandos slash estarão disponíveis.  
Para rodar em segundo plano no servidor, use `screen`, `tmux` ou configure um serviço `systemd`.

### Exemplo com systemd

```ini
# /etc/systemd/system/cyan.service
[Unit]
Description=Cyan Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/cyan-bot
ExecStart=/home/ubuntu/cyan-bot/.venv/bin/python main.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable cyan
sudo systemctl start cyan
sudo systemctl status cyan
```

---

## Como usar

### Canal `#briefing-do-pedido`

1. O atendimento envia todos os materiais do pedido no canal (prints, imagens, PDFs, arquivos)
2. Usa o comando `/briefing`
3. O Cyan lê todas as mensagens recentes, analisa tudo e devolve o briefing estruturado
4. Os arquivos são automaticamente encaminhados para `#análise-de-arquivos`

### Canal `#análise-de-arquivos`

- **Automático:** qualquer arquivo enviado no canal é analisado imediatamente
- **Manual:** use `/analisar` para analisar os arquivos das últimas 20 mensagens

---

## Estrutura do projeto

```
cyan-bot/
├── main.py                    # Ponto de entrada — bot e slash commands
├── config.py                  # Variáveis de ambiente e configurações
├── handlers/
│   ├── briefing_handler.py    # Lógica do canal #briefing-do-pedido
│   └── analysis_handler.py    # Lógica do canal #análise-de-arquivos
├── utils/
│   ├── openai_client.py       # Cliente GPT-4o + system prompt do Cyan
│   └── file_processor.py      # Processamento de imagens e PDFs
├── requirements.txt
├── .env.example
└── README.md
```

---

## Observações técnicas

- Arquivos acima de **10 MB** não são processados — o bot orienta o envio via Google Drive
- PDFs são convertidos em imagens (primeiras 3 páginas) via `pdf2image`; se não instalado, extrai texto com `pypdf`
- O bot só responde nos canais configurados via variáveis de ambiente
- Logs são gravados em `cyan.log` e também no stdout
